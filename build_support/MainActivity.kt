package com.khdoom.app

import android.app.Activity
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterFragmentActivity() {
    private val channelName = "khdoom/profile_image"
    private val imageRequestCode = 7301
    private var pendingResult: MethodChannel.Result? = null
    private var pendingFileName: String = "khdoom_profile_image"
    private var voicePlayer: MediaPlayer? = null

    // صوت «اسألني» بالذكاء الاصطناعي: ملف MP3 جاهز من التطبيق يُشغَّل هنا، وأي تعذر يرجع للتطبيق ليكمل بصوت الجوال.
    private fun stopVoice() {
        val player = voicePlayer
        voicePlayer = null
        try {
            player?.release()
        } catch (_: Exception) {
        }
    }

    private fun playVoice(path: String) {
        stopVoice()
        val player = MediaPlayer()
        voicePlayer = player
        player.setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
        )
        player.setDataSource(path)
        player.setOnCompletionListener { finished ->
            if (voicePlayer === finished) stopVoice()
        }
        player.prepare()
        player.start()
    }

    override fun onDestroy() {
        stopVoice()
        super.onDestroy()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        DriverTrackingBridge(this).attach(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "khdoom/assistant_voice")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "play" -> {
                        try {
                            playVoice(call.argument<String>("path") ?: "")
                            result.success(true)
                        } catch (_: Exception) {
                            stopVoice()
                            result.error("voice_failed", "تعذر تشغيل الصوت", null)
                        }
                    }
                    "stop" -> {
                        stopVoice()
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                if (call.method == "pickImage") {
                    if (pendingResult != null) {
                        result.error("busy", "اختيار صورة جارٍ بالفعل", null)
                        return@setMethodCallHandler
                    }
                    pendingResult = result
                    pendingFileName = call.argument<String>("fileName") ?: "khdoom_profile_image"
                    val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                        addCategory(Intent.CATEGORY_OPENABLE)
                        type = "image/*"
                    }
                    startActivityForResult(intent, imageRequestCode)
                } else {
                    result.notImplemented()
                }
            }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != imageRequestCode) return
        val result = pendingResult
        pendingResult = null
        if (resultCode != Activity.RESULT_OK || data?.data == null) {
            result?.success(null)
            return
        }
        try {
            val safeFileName = pendingFileName.replace(Regex("[^A-Za-z0-9_-]"), "_")
            val destination = File(filesDir, safeFileName)
            contentResolver.openInputStream(data.data!!).use { input ->
                destination.outputStream().use { output -> input!!.copyTo(output) }
            }
            result?.success(destination.absolutePath)
        } catch (error: Exception) {
            result?.error("copy_failed", "تعذر حفظ الصورة", error.message)
        }
    }
}

