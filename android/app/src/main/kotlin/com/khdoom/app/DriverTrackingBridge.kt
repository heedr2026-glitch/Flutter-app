package com.khdoom.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.plugin.common.MethodChannel
import org.json.JSONObject

class DriverTrackingBridge(private val activity: FlutterFragmentActivity) {
    fun attach(engine: FlutterEngine) {
        MethodChannel(engine.dartExecutor.binaryMessenger, "khdoom/vehicle_tracking").setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "start" -> {
                        require(activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) { "يلزم السماح بالموقع الدقيق" }
                        val args = call.arguments as Map<*, *>
                        val config = JSONObject().put("baseUrl", args["baseUrl"]).put("token", args["token"])
                            .put("vehicleKey", args["vehicleKey"]).put("revision", args["revision"]).put("branchId", args["branchId"])
                        require(java.net.URI(config.getString("baseUrl")).scheme == "https") { "يلزم اتصال آمن" }
                        TrackingStore.save(activity, config)
                        val intent = Intent(activity, DriverTrackingService::class.java)
                        if (Build.VERSION.SDK_INT >= 26) activity.startForegroundService(intent) else activity.startService(intent)
                        result.success(true)
                    }
                    "stop" -> {
                        // Do not start a foreground service merely to stop one.
                        TrackingStore.clear(activity);TrackingStore.status(activity, "stopped")
                        activity.stopService(Intent(activity, DriverTrackingService::class.java));result.success(true)
                    }
                    "status" -> result.success(if (DriverTrackingService.running) TrackingStore.status(activity) else "stopped")
                    else -> result.notImplemented()
                }
            } catch (_: Exception) { result.error("tracking_failed", "تعذر تشغيل التتبع؛ افتح التطبيق وتحقق من صلاحية الموقع الدقيق", null) }
        }
    }
}
