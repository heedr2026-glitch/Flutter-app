package com.khdoom.app

import android.app.Activity
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMuxer
import android.media.MediaPlayer
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.nio.ByteBuffer

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

    // «أنشئ صوت»: ياخذ الصوت بس من جزء الفيديو على الجوال نفسه (بدون رفع الفيديو كامل).
    private fun extractAudioClip(source: String, startMs: Long, endMs: Long): String {
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(source)
            var track = -1
            var format: MediaFormat? = null
            for (i in 0 until extractor.trackCount) {
                val candidate = extractor.getTrackFormat(i)
                if ((candidate.getString(MediaFormat.KEY_MIME) ?: "").startsWith("audio/")) {
                    track = i
                    format = candidate
                    break
                }
            }
            if (track < 0 || format == null) throw IllegalStateException("no_audio")
            if (format.getString(MediaFormat.KEY_MIME) != "audio/mp4a-latm") throw IllegalStateException("unsupported")
            extractor.selectTrack(track)
            val out = File(cacheDir, "khdoom_clip_${System.currentTimeMillis()}.m4a")
            val muxer = MediaMuxer(out.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
            var written = 0
            try {
                val target = muxer.addTrack(format)
                muxer.start()
                val size = if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE) else 0
                val buffer = ByteBuffer.allocate(maxOf(size, 256 * 1024))
                val info = MediaCodec.BufferInfo()
                val startUs = startMs * 1000
                val endUs = endMs * 1000
                extractor.seekTo(startUs, MediaExtractor.SEEK_TO_PREVIOUS_SYNC)
                while (true) {
                    info.offset = 0
                    info.size = extractor.readSampleData(buffer, 0)
                    if (info.size < 0) break
                    val time = extractor.sampleTime
                    if (time > endUs) break
                    if (time >= startUs) {
                        info.presentationTimeUs = time - startUs
                        info.flags = MediaCodec.BUFFER_FLAG_KEY_FRAME
                        muxer.writeSampleData(target, buffer, info)
                        written++
                    }
                    extractor.advance()
                }
                muxer.stop()
            } finally {
                try { muxer.release() } catch (_: Exception) {}
            }
            if (written == 0) {
                out.delete()
                throw IllegalStateException("empty")
            }
            return out.path
        } finally {
            extractor.release()
        }
    }

    override fun onDestroy() {
        stopVoice()
        super.onDestroy()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        DriverTrackingBridge(this).attach(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "khdoom/audio_clip")
            .setMethodCallHandler { call, result ->
                if (call.method != "extract") {
                    result.notImplemented()
                    return@setMethodCallHandler
                }
                val path = call.argument<String>("path") ?: ""
                val startMs = (call.argument<Number>("startMs") ?: 0).toLong()
                val endMs = (call.argument<Number>("endMs") ?: 0).toLong()
                Thread {
                    try {
                        val out = extractAudioClip(path, startMs, endMs)
                        runOnUiThread { result.success(out) }
                    } catch (error: Exception) {
                        runOnUiThread { result.error("clip_failed", error.message ?: "clip_failed", null) }
                    }
                }.start()
            }
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

