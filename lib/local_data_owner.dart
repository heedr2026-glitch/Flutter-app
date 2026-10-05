import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// بيانات المؤسسة المحفوظة على الجوال تتبع مؤسسة واحدة في كل وقت.
///
/// عند دخول مؤسسة مختلفة على نفس الجوال تُنقل بيانات المؤسسة السابقة إلى خانة
/// جانبية باسمها (لا تُحذف)، وتُسترجع بيانات المؤسسة الداخلة إن كانت محفوظة من
/// قبل. بهذا لا ترى مؤسسة بيانات غيرها ولا تُرفع بيانات مؤسسة إلى حساب أخرى.
class LocalDataOwner {
  static const ownerKey = 'local_data_owner_v1';
  static const _stashPrefix = 'orgstash:';
  static const _localPrefix = 'local:';

  // إعدادات تخص الجهاز نفسه، لا المؤسسة، فتبقى في مكانها عند التبديل.
  static const _deviceKeys = {
    ownerKey,
    'khdoom_device_id',
    'cloud_api_url',
    'remembered_login_username',
    'local_account_created',
    'has_logged_out_once',
    'settings_language',
    'settings_notifications',
    'settings_appointment_reminders',
  };

  static bool _stays(String key) =>
      _deviceKeys.contains(key) ||
      key.startsWith(_stashPrefix) ||
      key.startsWith('session_');

  /// مالك مؤقت لحساب أُنشئ على الجوال ولم يُسجَّل في الخادم بعد.
  static String localOwner(String username) =>
      '$_localPrefix${username.trim().toLowerCase()}';

  /// يرجع false إذا لم تُكتب القيمة، حتى لا يُحذف أصلها قبل نجاح نسخها.
  static Future<bool> _put(
    SharedPreferences raw,
    String key,
    Object? value,
  ) async {
    if (value is String) return raw.setString(key, value);
    if (value is bool) return raw.setBool(key, value);
    if (value is int) return raw.setInt(key, value);
    if (value is double) return raw.setDouble(key, value);
    if (value is List) {
      return raw.setStringList(
        key,
        value.map((item) => item.toString()).toList(),
      );
    }
    return true;
  }

  /// يرجّع ما بقي في الخانة الجانبية لهذا المالك. آمن عند التكرار: إن انقطع
  /// التبديل في منتصفه يكمل هنا في الدخول التالي دون أن يكتب فوق بيانات حيّة.
  static Future<void> _restore(SharedPreferences raw, String owner) async {
    final prefix = '$_stashPrefix$owner:';
    for (final stashed in raw.getKeys().toList()) {
      if (!stashed.startsWith(prefix)) continue;
      final key = stashed.substring(prefix.length);
      if (key.isNotEmpty && !raw.containsKey(key)) {
        if (!await _put(raw, key, raw.get(stashed))) continue;
      }
      await raw.remove(stashed);
    }
  }

  /// يجعل [owner] مالك بيانات الجوال. يرجع true إذا تبدّلت البيانات فعلًا.
  ///
  /// [previousOwner] معرّف المؤسسة المسجّل من آخر دخول، يُستخدم فقط للأجهزة
  /// التي حُفظت بياناتها قبل وجود هذه الخاصية. [username] يسمح بضم حساب محلي
  /// لم يُسجَّل في الخادم إلى مؤسسته عند أول دخول ناجح بنفس الاسم.
  /// [freshAccount] لحساب جديد: أي بيانات موجودة ليست له مهما كان مصدرها.
  /// [adminLogin] اسم دخول المدير (للمدير فقط): على جهاز قديم بلا مالك مسجَّل،
  /// مدير باسم لا يطابق المدير المحفوظ يعني مؤسسة أخرى.
  static Future<bool> switchTo(
    SharedPreferences raw,
    String owner, {
    String? previousOwner,
    String? username,
    String? adminLogin,
    bool freshAccount = false,
  }) async {
    owner = owner.trim();
    if (owner.isEmpty) return false;
    final stored = (raw.getString(ownerKey) ?? '').trim();
    var current = stored;
    if (current == owner) {
      await _restore(raw, owner);
      return false;
    }
    if (current.isEmpty) {
      final previous = (previousOwner ?? '').trim();
      if (previous.isNotEmpty && previous != owner) {
        current = previous;
      } else if (freshAccount) {
        current = 'legacy';
      } else if (previous.isEmpty && adminLogin != null) {
        final login = adminLogin.trim().toLowerCase();
        final known = [
          raw.getString('admin_login_username'),
          raw.getString('account_phone'),
          raw.getString('account_email'),
        ].map((value) => (value ?? '').trim().toLowerCase()).toList();
        if (known.first.isNotEmpty && !known.contains(login)) {
          current = 'legacy';
        }
      }
    }
    final sameLocalAccount =
        username != null &&
        current == localOwner(username) &&
        !owner.startsWith(_localPrefix);
    if (current.isEmpty || sameLocalAccount) {
      // نفس صاحب البيانات: نثبّت المالك دون نقل، ونكمل أي استرجاع انقطع سابقًا.
      await raw.setString(ownerKey, owner);
      await _restore(raw, owner);
      return false;
    }
    // على جهاز بلا مالك مسجَّل نثبّت المالك القديم أولًا؛ لو انقطع النقل يُستكمل
    // في الدخول التالي بدل أن تُعتبر البيانات الباقية للمؤسسة الداخلة.
    if (stored.isEmpty) await raw.setString(ownerKey, current);
    for (final key in raw.getKeys().toList()) {
      if (_stays(key)) continue;
      if (!await _put(raw, '$_stashPrefix$current:$key', raw.get(key))) {
        continue;
      }
      await raw.remove(key);
    }
    // يُثبَّت المالك الجديد قبل الاسترجاع؛ لو انقطع التطبيق هنا يكمل الاسترجاع
    // في الدخول التالي ولا تُنقل بيانات المؤسسة الجديدة باسم القديمة.
    await raw.setString(ownerKey, owner);
    await _restore(raw, owner);
    await _forgetLocalPasswords();
    return true;
  }

  /// يثبّت المالك لبيانات لم يُعرف صاحبها بعد (فارغ أو حساب محلي)، بلا نقل.
  static Future<void> adopt(SharedPreferences raw, String owner) async {
    owner = owner.trim();
    if (owner.isEmpty) return;
    final current = (raw.getString(ownerKey) ?? '').trim();
    if (current.isEmpty || current.startsWith(_localPrefix)) {
      await raw.setString(ownerKey, owner);
    }
  }

  // كلمات المرور المحفوظة للدخول بلا اتصال (المدير والموظفون) تخص المؤسسة
  // السابقة فلا تبقى لمؤسسة أخرى؛ دخول المدير الناجح يكتب كلمته من جديد.
  static Future<void> _forgetLocalPasswords() async {
    try {
      const storage = FlutterSecureStorage();
      await storage.delete(key: 'admin_account_password');
      final all = await storage.readAll();
      for (final key in all.keys) {
        if (key.startsWith('employee_password_')) {
          await storage.delete(key: key);
        }
      }
    } catch (_) {
      // تعذر قراءة التخزين الآمن لا يمنع الدخول.
    }
  }
}
