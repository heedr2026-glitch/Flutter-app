package com.khdoom.app

import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.location.*
import android.os.*
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyStore
import java.util.Calendar
import java.util.TimeZone
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Credentials are encrypted with a non-exportable Android Keystore key. */
object TrackingStore {
    private const val alias = "khdoom_driver_tracking_v1"
    private fun prefs(c: Context) = c.getSharedPreferences("driver_tracking", Context.MODE_PRIVATE)
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun save(c: Context, config: JSONObject) {
        // ربط جديد أو مختلف: ما حُفظ للربط السابق (جدول ونقاط لم تُرسل) لا يخصه.
        val old = read(c)
        if (old == null || old.optString("vehicleKey") != config.optString("vehicleKey") ||
            old.optInt("revision") != config.optInt("revision") || old.optString("token") != config.optString("token")) {
            clearSchedule(c); TrackingQueue.clear(c)
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val bytes = cipher.doFinal(config.toString().toByteArray(Charsets.UTF_8))
        prefs(c).edit().putString("cipher", Base64.encodeToString(bytes, Base64.NO_WRAP))
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP)).apply()
    }
    fun read(c: Context): JSONObject? = try {
        val p = prefs(c); val value = p.getString("cipher", null)
        if (value == null) null else {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(p.getString("iv", ""), Base64.NO_WRAP)))
            }
            JSONObject(String(cipher.doFinal(Base64.decode(value, Base64.NO_WRAP)), Charsets.UTF_8))
        }
    } catch (_: Exception) { null }
    fun clear(c: Context) { prefs(c).edit().remove("cipher").remove("iv").remove("schedule").remove("schedule_at").apply(); TrackingQueue.clear(c) }
    /** آخر جدول تحقق منه الخادم؛ يسمح بتسجيل المسار خلال الدوام عند انقطاع الإنترنت لمدة محدودة. */
    const val OFFLINE_GRACE_MS = 24L * 60 * 60 * 1000
    fun saveSchedule(c: Context, schedule: JSONObject) {
        prefs(c).edit().putString("schedule", schedule.toString()).putLong("schedule_at", System.currentTimeMillis()).apply()
    }
    fun scheduleAt(c: Context): Long = prefs(c).getLong("schedule_at", 0L)
    fun readSchedule(c: Context): JSONObject? = try {
        val raw = prefs(c).getString("schedule", null)
        if (raw == null || System.currentTimeMillis() - scheduleAt(c) !in 0..OFFLINE_GRACE_MS) null else JSONObject(raw)
    } catch (_: Exception) { null }
    fun clearSchedule(c: Context) { prefs(c).edit().remove("schedule").remove("schedule_at").apply() }
    fun status(c: Context, status: String) { prefs(c).edit().putString("status", status).apply() }
    fun status(c: Context) = prefs(c).getString("status", "stopped") ?: "stopped"
}

/** نقاط المسار التي لم تُرسل بعد. تُحفظ في ملف خاص بالتطبيق وتُرسل بالترتيب عند توفر الاتصال. */
object TrackingQueue {
    private const val MAX_POINTS = 6000
    private fun file(c: Context) = java.io.File(c.filesDir, "driver_tracking_queue.jsonl")
    private fun lines(f: java.io.File): List<String> = if (f.exists()) f.readLines(Charsets.UTF_8).filter { it.isNotBlank() } else emptyList()
    @Synchronized fun add(c: Context, point: JSONObject) {
        val f = file(c)
        f.appendText(point.toString() + "\n", Charsets.UTF_8)
        // يوم كامل بلا اتصال تقريبًا؛ بعده يُحذف الأقدم حتى لا يمتلئ الجوال.
        if (f.length() > 800000L) {
            val all = lines(f)
            if (all.size > MAX_POINTS) f.writeText(all.takeLast(MAX_POINTS - 1000).joinToString("\n") + "\n", Charsets.UTF_8)
        }
    }
    @Synchronized fun peek(c: Context, count: Int): List<String> = try { lines(file(c)).take(count) } catch (_: Exception) { emptyList() }
    @Synchronized fun drop(c: Context, count: Int): Boolean = try {
        val f = file(c)
        val rest = lines(f).drop(count)
        if (rest.isEmpty()) f.delete() else f.writeText(rest.joinToString("\n") + "\n", Charsets.UTF_8)
        true
    } catch (_: Exception) { false }
    @Synchronized fun clear(c: Context) { try { file(c).delete() } catch (_: Exception) { } }
}

class DriverTrackingService : Service(), LocationListener {
    companion object { @Volatile var running = false }
    private val handler = Handler(Looper.getMainLooper())
    private val network = Executors.newSingleThreadExecutor()
    private lateinit var locations: LocationManager
    @Volatile private var config: JSONObject? = null
    @Volatile private var schedule: JSONObject? = null
    // آخر تحقق ناجح من الخادم بتوقيت الساعة (يُحفظ مع الجدول)، وآخر اتصال ناجح بتوقيت التشغيل.
    @Volatile private var verifiedAt = 0L
    @Volatile private var onlineAt = 0L
    @Volatile private var alive = true
    private var listening = false
    private var lastStored: Location? = null
    private var lastStoredAt = 0L
    private var lastText = ""
    private val tick = object : Runnable {
        override fun run() {
            if (!alive) return
            applySchedule()
            network.execute { refresh() }
            handler.postDelayed(this, 15000L)
        }
    }
    override fun onBind(intent: Intent?) = null
    override fun onCreate() {
        super.onCreate(); running = true; locations = getSystemService(LOCATION_SERVICE) as LocationManager
        if (Build.VERSION.SDK_INT >= 26) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(
                NotificationChannel("driver_tracking", "تتبع جوال السائق خلال الدوام", NotificationManager.IMPORTANCE_LOW))
        }
        startForeground(7302, notification("جارٍ التحقق من جدول الدوام"))
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        config = TrackingStore.read(this)
        if (intent?.action == "STOP") {
            alive = false; stopLocation(); val saved = config
            TrackingStore.clear(this); TrackingStore.status(this, "stopped")
            network.execute { try { if (saved != null) request(saved, "/api/vehicle-tracking/heartbeat", JSONObject().put("status", "stopped")) } catch (_: Exception) {} }
            stopSelf(); return START_NOT_STICKY
        }
        if (config == null) { stopSelf(); return START_NOT_STICKY }
        // جدول سبق التحقق منه (خلال مدة السماح) يكفي لمتابعة التسجيل لو بدأت الخدمة بلا إنترنت.
        schedule = TrackingStore.readSchedule(this)
        verifiedAt = if (schedule != null) TrackingStore.scheduleAt(this) else 0L
        onlineAt = 0;lastRefresh = 0
        handler.removeCallbacks(tick);handler.post(tick)
        return START_STICKY
    }
    private fun notification(text: String): Notification {
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), flags)
        val stop = PendingIntent.getService(this, 1, Intent(this, DriverTrackingService::class.java).setAction("STOP"), flags)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "driver_tracking") else Notification.Builder(this)
        return builder.setSmallIcon(android.R.drawable.ic_menu_mylocation).setContentTitle("خدووم — تتبع دوامي")
            .setContentText(text).setContentIntent(open).setOngoing(true).addAction(android.R.drawable.ic_menu_close_clear_cancel, "إيقاف التتبع", stop).build()
    }
    private fun state(value: String, text: String) {
        if (TrackingStore.status(this) != value || lastText != text) {
            TrackingStore.status(this, value); lastText = text
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).notify(7302, notification(text))
        }
    }
    private fun request(cfg: JSONObject, path: String, body: JSONObject? = null): JSONObject {
        val url = URL(cfg.getString("baseUrl").trimEnd('/') + path)
        require(url.protocol == "https")
        val connection = url.openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 10000;connection.readTimeout = 10000
            connection.setRequestProperty("Authorization", "Bearer " + cfg.getString("token"))
            connection.setRequestProperty("X-Branch-Id", cfg.optString("branchId", "main"))
            if (body != null) {
                connection.requestMethod = "POST";connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            if (connection.responseCode == 401) throw SecurityException("session_expired")
            if (connection.responseCode !in 200..299) throw IllegalStateException("http_" + connection.responseCode)
            return connection.inputStream.bufferedReader().use { JSONObject(it.readText()) }
        } finally { connection.disconnect() }
    }
    private var lastRefresh = 0L
    private fun refresh() {
        if (!alive || SystemClock.elapsedRealtime() - lastRefresh < 55000) return
        lastRefresh = SystemClock.elapsedRealtime()
        val cfg = config ?: return
        try {
            val fresh = request(cfg, "/api/vehicle-tracking/assignment")
            if (!fresh.optBoolean("consented") || fresh.optInt("revision") != cfg.getInt("revision") || fresh.optString("vehicle_key") != cfg.getString("vehicleKey")) {
                // الموافقة أو الربط تغيّر: يتوقف التسجيل وتُحذف النقاط التي لم تُرسل.
                verifiedAt = 0; schedule = null; onlineAt = 0
                TrackingStore.clearSchedule(this); TrackingQueue.clear(this)
                handler.post { stopLocation(); state("not_enabled", "تغيّر الربط أو الجدول؛ افتح تتبع دوامي ووافق مجددًا") }
                return
            }
            schedule = fresh;verifiedAt = System.currentTimeMillis();onlineAt = SystemClock.elapsedRealtime()
            TrackingStore.saveSchedule(this, fresh)
            handler.post { if (alive) applySchedule() }
            val phone = TrackingStore.status(this)
            val reported = if (phone in listOf("tracking", "outside_schedule", "location_disabled", "permission_denied")) phone else "armed"
            request(cfg, "/api/vehicle-tracking/heartbeat", JSONObject().put("status", reported))
            flush()
        } catch (_: SecurityException) {
            verifiedAt = 0;schedule = null;onlineAt = 0
            handler.post { TrackingStore.clear(this);state("stopped", "انتهت الجلسة؛ سجّل الدخول مجددًا");stopSelf() }
        } catch (error: IllegalStateException) {
            onlineAt = 0
            if (error.message == "http_403") {
                // الخادم يرفض هذا الجوال الآن (مثل إيقاف المؤسسة): لا تسجيل حتى يقبله من جديد.
                verifiedAt = 0; schedule = null; TrackingStore.clearSchedule(this)
            }
            handler.post { if (alive) applySchedule() }
        } catch (_: Exception) {
            // لا اتصال الآن: يستمر التسجيل خلال الدوام بآخر جدول تحقق منه الخادم، وتُرسل النقاط عند رجوع الإنترنت.
            onlineAt = 0
            handler.post { if (alive) applySchedule() }
        }
    }
    /** يرسل النقاط المحفوظة بالترتيب على دفعات. يعمل على خيط الشبكة فقط، وبعد اتصال ناجح حديث. */
    private fun flush() {
        val cfg = config ?: return
        while (alive && onlineAt > 0 && SystemClock.elapsedRealtime() - onlineAt < 120000) {
            val lines = TrackingQueue.peek(this, 200)
            if (lines.isEmpty()) return
            val points = org.json.JSONArray()
            lines.forEach { try { points.put(JSONObject(it)) } catch (_: Exception) { } }
            try {
                if (points.length() > 0) {
                    request(cfg, "/api/vehicle-tracking/batch", JSONObject().put("vehicleKey", cfg.getString("vehicleKey")).put("points", points))
                }
                // تعذر حذف ما أُرسل: نتوقف حتى لا تُعاد نفس الدفعة في حلقة.
                if (!TrackingQueue.drop(this, lines.size)) { onlineAt = 0; return }
                onlineAt = SystemClock.elapsedRealtime()
            } catch (error: IllegalStateException) {
                // دفعة يرفضها الخادم لمحتواها لا تُعاد للأبد؛ بقية الأخطاء تُعاد المحاولة لاحقًا.
                val rejected = error.message == "http_400" || error.message == "http_413"
                if (!rejected || !TrackingQueue.drop(this, lines.size)) { onlineAt = 0; return }
            } catch (_: Exception) {
                onlineAt = 0; return
            }
        }
    }
    private fun inShift(s: JSONObject, time: Long = System.currentTimeMillis()): Boolean {
        if (s.optInt("enabled") != 1) return false
        val clock = Calendar.getInstance(TimeZone.getTimeZone("Asia/Riyadh")).apply { timeInMillis = time }
        val day = (clock.get(Calendar.DAY_OF_WEEK) + 5) % 7 + 1
        val minute = clock.get(Calendar.HOUR_OF_DAY) * 60 + clock.get(Calendar.MINUTE)
        val days = s.getJSONArray("weekdays"); fun has(d: Int) = (0 until days.length()).any { days.getInt(it) == d }
        val start = s.getInt("start_minute");val end = s.getInt("end_minute")
        return if (start < end) has(day) && minute >= start && minute < end
        else (has(day) && minute >= start) || (has(if (day == 1) 7 else day - 1) && minute < end)
    }
    private fun verified(): Boolean = verifiedAt > 0 && System.currentTimeMillis() - verifiedAt in 0..TrackingStore.OFFLINE_GRACE_MS
    private fun allowed(): Boolean = alive && verified() && schedule?.let { inShift(it) } == true
    private fun applySchedule() {
        val s = schedule
        if (s == null || !verified()) {
            stopLocation();state("offline", "التتبع متوقف حتى التحقق من الاتصال والجدول");return
        }
        if (!inShift(s)) { stopLocation();state("outside_schedule", "خارج الدوام — لا يتم جمع موقعك");return }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            stopLocation();state("permission_denied", "التتبع متوقف؛ يلزم السماح بالموقع الدقيق");return
        }
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).filter { locations.isProviderEnabled(it) }
        if (providers.isEmpty()) { stopLocation();state("location_disabled", "التتبع متوقف؛ فعّل الموقع");return }
        if (!listening) {
            try {
                // GPS كل 10 ثوانٍ ليتبع المسار الشوارع؛ موقع الشبكة الأضعف كل دقيقة كبديل فقط.
                // بدون شرط مسافة: المركبة الواقفة تبقى «متصلة» ويظهر آخر موقع لها كل دقيقة.
                providers.forEach { locations.requestLocationUpdates(it, if (it == LocationManager.GPS_PROVIDER) 10000L else 60000L, 0f, this, Looper.getMainLooper()) }
                listening = true
            } catch (_: SecurityException) { stopLocation();state("permission_denied", "التتبع متوقف؛ تحقق من الصلاحيات");return }
        }
        val online = onlineAt > 0 && SystemClock.elapsedRealtime() - onlineAt < 120000
        state("tracking", if (online) "تتبع موقع جوالك خلال الدوام — يمكنك الإيقاف"
            else "بدون إنترنت: يُسجَّل مسارك خلال الدوام ويُرسل عند رجوع الاتصال — يمكنك الإيقاف")
    }
    private fun stopLocation() { if (listening) locations.removeUpdates(this);listening = false }
    override fun onLocationChanged(location: Location) {
        if (!allowed()) { stopLocation();return }
        if (!location.hasAccuracy() || System.currentTimeMillis() - location.time !in 0..120000) return
        val sinceLast = SystemClock.elapsedRealtime() - lastStoredAt
        // القراءة الدقيقة (GPS) هي الأساس. القراءة الضعيفة تُقبل فقط إن لم تُحفظ قراءة منذ نحو دقيقة،
        // حتى تبقى المركبة «متصلة» داخل المباني دون أن تشوّه المسار ما دام GPS يعمل.
        if (location.accuracy > 60 && (location.accuracy > 250 || (lastStoredAt > 0 && sinceLast < 55000L))) return
        // نقطة كل 10 ثوانٍ تقريبًا والمركبة تتحرك (GPS يصل كل 10 ثوانٍ)، وكل دقيقة وهي واقفة.
        val previous = lastStored
        val moved = previous == null || previous.distanceTo(location) >= 30f
        if (lastStoredAt > 0 && (sinceLast < 8000L || (!moved && sinceLast < 55000L))) return
        val s = schedule ?: return
        if (!inShift(s, location.time)) return
        lastStored = location;lastStoredAt = SystemClock.elapsedRealtime()
        val captured = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(java.util.Date(location.time))
        val point = JSONObject().put("latitude", location.latitude)
            .put("longitude", location.longitude).put("accuracyMeters", location.accuracy.toDouble()).put("capturedAt", captured)
        // كل نقطة تُحفظ أولًا ثم تُرسل؛ بلا إنترنت تبقى محفوظة وتُرسل بالترتيب عند رجوعه.
        network.execute {
            // توقف التتبع أو تغيّر الربط قبل تنفيذ هذه المهمة: لا تُحفظ نقطة بعد مسح القائمة.
            if (!alive || schedule == null) return@execute
            try { TrackingQueue.add(this, point) } catch (_: Exception) { }
            flush()
        }
    }
    override fun onProviderEnabled(provider: String) { applySchedule() }
    override fun onProviderDisabled(provider: String) { applySchedule() }
    @Deprecated("Required on older Android versions")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
    override fun onDestroy() {
        alive = false;running = false;handler.removeCallbacksAndMessages(null);stopLocation();network.shutdown()
        super.onDestroy()
    }
}
