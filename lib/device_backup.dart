import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

/// نسخة في الخادم لبيانات كانت على الجوال فقط: المركبات، سجل الموظفين،
/// تواريخ السجل والرخص، فواتير الكهرباء، المهام، التصنيفات — لكل فرع.
///
/// - بعد كل حفظ: يرفع المفتاح بعد ثواني (آخر حفظ يغلب).
/// - عند فتح التطبيق: يسترجع أي نسخة أحدث من الخادم (جوال جديد أو بعد تحديث).
/// - الصور (imagePath) تنضمّن في النسخة وترجع ملفات على الجوال عند الاسترجاع.
class DeviceBackup {
  DeviceBackup._();

  static const _keys = {
    'business_vehicles',
    'employee_alert_records',
    'organization_alert_records',
    'organization_alert_categories_v1',
    'electricity_bills',
    'daily_work_tasks_v1',
    'daily_work_hidden_v1',
  };
  static const _stampPrefix = 'device_backup_stamp_v1:';
  static const _pendingKey = 'device_backup_pending_v1';
  static const _maxImageBytes = 900 * 1024;

  /// يمنع الرفع أثناء الاسترجاع حتى لا ترجع النسخة للخادم مرة ثانية.
  static bool _restoring = false;
  static Timer? _debounce;
  static bool _running = false;

  static bool backs(String key) =>
      _keys.contains(key) || (key.startsWith('alert_') && key.length <= 120);

  static String _id(String branch, String key) => '$branch|$key';

  /// يُستدعى من BranchPreferences بعد أي حفظ أو حذف لمفتاح محفوظ.
  static void markDirty(SharedPreferences raw, String branch, String key) {
    if (_restoring || !backs(key)) return;
    raw.setString('$_stampPrefix${_id(branch, key)}', DateTime.now().toUtc().toIso8601String());
    final pending = raw.getStringList(_pendingKey) ?? <String>[];
    final id = _id(branch, key);
    if (!pending.contains(id)) raw.setStringList(_pendingKey, [...pending, id]);
    _debounce?.cancel();
    _debounce = Timer(const Duration(seconds: 4), () => unawaited(sync()));
  }

  static Future<KhdoomCloudApi?> _api(SharedPreferences raw) async {
    if (raw.getString('session_user_type') == 'employee') return null;
    String? token;
    try {
      token = await const FlutterSecureStorage().read(key: 'cloud_session_token');
    } catch (_) {}
    if (token == null || token.isEmpty) return null;
    return KhdoomCloudApi(
      scope: BranchPreferences(raw, BranchPreferences.mainId),
      baseUrl: raw.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  /// استرجاع الأحدث من الخادم ثم رفع أي تعديلات على الجوال ما ارتفعت.
  static Future<void> sync() async {
    if (_running) return;
    _running = true;
    final raw = await SharedPreferences.getInstance();
    final api = await _api(raw);
    if (api == null) {
      _running = false;
      return;
    }
    try {
      await _restore(raw, api);
      await _upload(raw, api);
    } catch (_) {
      // بدون إنترنت: البيانات باقية على الجوال وترتفع في المرة الجاية.
    } finally {
      api.close();
      _running = false;
    }
  }

  static Future<void> _restore(SharedPreferences raw, KhdoomCloudApi api) async {
    final index = await api.deviceBackupIndex();
    for (final entry in index) {
      final item = Map<String, dynamic>.from(entry as Map);
      final branch = item['branch']?.toString() ?? BranchPreferences.mainId;
      final key = item['key']?.toString() ?? '';
      final remoteAt = item['updatedAt']?.toString() ?? '';
      if (!backs(key) || remoteAt.isEmpty) continue;
      final localAt = raw.getString('$_stampPrefix${_id(branch, key)}');
      final prefs = BranchPreferences(raw, branch);
      final localValue = prefs.getString(key);
      final localEmpty = localValue == null || localValue.isEmpty || localValue == '[]' || localValue == '{}';
      // جوال جديد (ما فيه شي) أو نسخة الخادم أحدث: نسترجع.
      final newer = localAt == null ? localEmpty || localValue == null : remoteAt.compareTo(localAt) > 0;
      if (!newer) continue;
      final full = await api.deviceBackupItem(branch, key);
      final value = await _unpackImages(full['value']?.toString() ?? '');
      _restoring = true;
      try {
        await prefs.setString(key, value);
        await raw.setString('$_stampPrefix${_id(branch, key)}', remoteAt);
      } finally {
        _restoring = false;
      }
      if (branch != BranchPreferences.mainId) await _rememberBranch(raw, branch);
    }
    // نسخة الجوال القديمة (قبل هذا التحديث) ما عليها ختم: نعلّمها للرفع مرة وحدة.
    for (final branch in BranchPreferences.branches(raw)) {
      final id = branch['id']!;
      final prefs = BranchPreferences(raw, id);
      for (final key in prefs.getKeys().where(backs)) {
        final stampKey = '$_stampPrefix${_id(id, key)}';
        if (raw.getString(stampKey) != null) continue;
        final value = prefs.getString(key);
        if (value == null || value.isEmpty) continue;
        await raw.setString(stampKey, DateTime.now().toUtc().toIso8601String());
        final pending = raw.getStringList(_pendingKey) ?? <String>[];
        if (!pending.contains(_id(id, key))) {
          await raw.setStringList(_pendingKey, [...pending, _id(id, key)]);
        }
      }
    }
  }

  static Future<void> _rememberBranch(SharedPreferences raw, String id) async {
    if (BranchPreferences.branches(raw).any((b) => b['id'] == id)) return;
    final saved = raw.getString(BranchPreferences.registryKey);
    final items = saved == null ? <dynamic>[] : jsonDecode(saved) as List;
    items.add({'id': id, 'name': 'فرع'});
    await raw.setString(BranchPreferences.registryKey, jsonEncode(items));
  }

  static Future<void> _upload(SharedPreferences raw, KhdoomCloudApi api) async {
    final pending = List<String>.from(raw.getStringList(_pendingKey) ?? const []);
    for (final id in pending) {
      final split = id.indexOf('|');
      if (split <= 0) continue;
      final branch = id.substring(0, split);
      final key = id.substring(split + 1);
      final value = BranchPreferences(raw, branch).getString(key) ?? '';
      final stamp = raw.getString('$_stampPrefix$id') ?? DateTime.now().toUtc().toIso8601String();
      await api.putDeviceBackup(branch, key, await _packImages(value), stamp);
      final now = List<String>.from(raw.getStringList(_pendingKey) ?? const [])..remove(id);
      await raw.setStringList(_pendingKey, now);
    }
  }

  /// يضمّن صورة كل imagePath (لين 900 كيلو) داخل النسخة حتى ترجع على الجوال الجديد.
  static Future<String> _packImages(String value) async {
    if (!value.contains('imagePath')) return value;
    dynamic data;
    try {
      data = jsonDecode(value);
    } catch (_) {
      return value;
    }
    Future<dynamic> walk(dynamic node) async {
      if (node is List) return [for (final x in node) await walk(x)];
      if (node is! Map) return node;
      final out = <String, dynamic>{};
      for (final e in node.entries) {
        out[e.key.toString()] = await walk(e.value);
      }
      final path = out['imagePath']?.toString() ?? '';
      if (path.isNotEmpty && (out['imageData']?.toString() ?? '').isEmpty) {
        try {
          final file = File(path);
          if (file.existsSync() && file.lengthSync() <= _maxImageBytes) {
            out['_backupImage'] = base64Encode(await file.readAsBytes());
          }
        } catch (_) {}
      }
      return out;
    }

    return jsonEncode(await walk(data));
  }

  /// يرجّع الصور المضمّنة ملفات على الجوال ويحدّث imagePath.
  static Future<String> _unpackImages(String value) async {
    if (!value.contains('_backupImage')) return value;
    dynamic data;
    try {
      data = jsonDecode(value);
    } catch (_) {
      return value;
    }
    var counter = 0;
    Future<dynamic> walk(dynamic node) async {
      if (node is List) return [for (final x in node) await walk(x)];
      if (node is! Map) return node;
      final out = <String, dynamic>{};
      for (final e in node.entries) {
        out[e.key.toString()] = await walk(e.value);
      }
      final encoded = out.remove('_backupImage')?.toString() ?? '';
      if (encoded.isNotEmpty) {
        try {
          final original = out['imagePath']?.toString() ?? '';
          var target = original.isEmpty ? null : File(original);
          if (target == null || !target.parent.existsSync()) {
            target = File(
              '${Directory.systemTemp.parent.path}/files/khdoom_restored_${DateTime.now().microsecondsSinceEpoch}_${counter++}.jpg',
            );
          }
          if (!target.existsSync()) {
            await target.parent.create(recursive: true);
            await target.writeAsBytes(base64Decode(encoded), flush: true);
          }
          out['imagePath'] = target.path;
        } catch (_) {}
      }
      return out;
    }

    return jsonEncode(await walk(data));
  }
}
