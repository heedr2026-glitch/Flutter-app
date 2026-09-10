import 'whatsapp_workspace.dart';
import 'reception_conversation_page.dart';

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:image_picker/image_picker.dart';

import 'branch_store.dart';
import 'daily_work_page.dart';
import 'daily_work_store.dart';
import 'employee_management.dart';
import 'branch_pages.dart';
import 'organization_categories.dart';
import 'assistant_conversation.dart';
import 'appointment_followup_card.dart';
import 'home_branches_switch.dart';
import 'question_answer_training.dart';
import 'my_advertisements.dart';
import 'page_refresh_button.dart';
import 'ai_employee_training_page.dart';

import 'package:url_launcher/url_launcher.dart';
import 'package:timezone/data/latest.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

import 'cloud_api.dart';
import 'community_page.dart';
import 'document_viewer.dart';
import 'organization_record_editor.dart';
import 'package_resource_limits.dart';
import 'commercial_research/commercial_research_models.dart';
import 'commercial_research/commercial_whatsapp.dart';
import 'commercial_research/commercial_research_report.dart';

Map<String, Widget Function(BuildContext, DailyWorkItem)>
get dailyWorkDestinations => {
  'employees': (_, item) => EmployeeManagementPage(
    onAlertsChanged: KhdoomNotifications.syncStoredAlerts,
  ),
  'organization': (_, item) => OrganizationAlertsPage(
    categoryId: item.source == null
        ? null
        : organizationCategoryOf(item.source!),
    categoryTitle: item.source?['title']?.toString() ?? item.title,
    recordId: item.source?['id']?.toString(),
  ),
  'vehicles': (_, item) =>
      VehiclesPage(recordId: item.source?['id']?.toString()),
  'bills': (_, item) => const OrganizationAlertsPage(
    categoryId: '_electricity',
    categoryTitle: 'فواتير الكهرباء',
  ),
  'appointments': (_, item) => const AppointmentsRequestsPage(),
};

Future<bool> confirmSensitiveDeletion(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'حذف نهائي',
}) async {
  final controller = TextEditingController();
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (dialogContext) => StatefulBuilder(
      builder: (context, setDialogState) => AlertDialog(
        backgroundColor: const Color(0xFF172554),
        title: Text(title, style: const TextStyle(color: Colors.white)),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(message, style: const TextStyle(color: Colors.white)),
            const SizedBox(height: 10),
            const Text(
              'اكتب كلمة «تأكيد» لإكمال العملية.',
              style: TextStyle(color: Colors.white70),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              autofocus: true,
              style: const TextStyle(color: Colors.white),
              onChanged: (_) => setDialogState(() {}),
              decoration: const InputDecoration(labelText: 'تأكيد العملية'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: {'تأكيد', 'تاكيد'}.contains(controller.text.trim())
                ? () => Navigator.pop(dialogContext, true)
                : null,
            child: Text(confirmLabel),
          ),
        ],
      ),
    ),
  );
  // انتظر اكتمال حركة إغلاق النافذة قبل التخلص من الحقل؛ وإلا قد يحاول
  // Flutter رسم TextField بعد التخلص من المتحكم وتظهر شاشة الخطأ الحمراء.
  await Future<void>.delayed(const Duration(milliseconds: 350));
  controller.dispose();
  return confirmed == true;
}

Future<Map<String, String>> khdoomDeviceIdentity() async {
  final prefs = await BranchPreferences.getInstance();
  var id = prefs.getString('khdoom_device_id') ?? '';
  if (id.isEmpty) {
    id = 'device_${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}';
    await prefs.setString('khdoom_device_id', id);
  }
  final platform = Platform.isAndroid
      ? 'هاتف Android'
      : Platform.isIOS
      ? 'هاتف iPhone'
      : 'جهاز ${Platform.operatingSystem}';
  return {'id': id, 'name': platform};
}

class KhdoomNotifications {
  static final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  static Future<void> initialize() async {
    tz_data.initializeTimeZones();
    tz.setLocalLocation(tz.getLocation('Asia/Riyadh'));
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );
  }

  static int idFor(String value) {
    final numeric = int.tryParse(value);
    if (numeric != null) return numeric.remainder(0x7fffffff);
    var result = 0;
    for (final unit in value.codeUnits) {
      result = (result * 31 + unit) & 0x7fffffff;
    }
    return result;
  }

  static Future<void> requestPermission() async {
    await _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >()
        ?.requestNotificationsPermission();
  }

  static Future<void> showNewAppointmentRequest(
    Map<String, dynamic> item,
  ) async {
    final prefs = await BranchPreferences.getInstance();
    if (!(prefs.getBool('settings_notifications') ?? true)) return;
    await requestPermission();
    await _plugin.show(
      id: idFor('new_request_${item['id']}'),
      title: (item['followup_count'] as num? ?? 0) > 0
          ? 'متابعة جديدة من العميل'
          : 'طلب موعد جديد من الشات',
      body:
          '${item['customer_name']?.toString().isNotEmpty == true ? item['customer_name'] : 'عميل'} — ${(item['followup_count'] as num? ?? 0) > 0 ? 'طلب متابعة للموعد' : item['request_type'] ?? 'طلب موعد'}',
      notificationDetails: const NotificationDetails(
        android: AndroidNotificationDetails(
          'khdoom_new_requests_v1',
          'طلبات العملاء الجديدة',
          channelDescription: 'إشعار صوتي عند وصول طلب موعد جديد من الشات',
          importance: Importance.max,
          priority: Priority.max,
          playSound: true,
          enableVibration: true,
          category: AndroidNotificationCategory.message,
        ),
      ),
      payload: 'appointment_${item['id']}',
    );
  }

  static Future<void> showSecurityAlert(Map<String, dynamic> item) async {
    final prefs = await BranchPreferences.getInstance();
    if (!(prefs.getBool('settings_notifications') ?? true)) return;
    await requestPermission();
    final action = item['action']?.toString() ?? '';
    final title = switch (action) {
      'new_device' => 'دخول من جهاز جديد 🔐',
      'suspicious_login' => 'محاولات دخول مشبوهة 🚨',
      'employee_updated' => 'تم تغيير صلاحيات موظف 🔐',
      'device_blocked' => 'تم حظر جهاز 🔒',
      'blocked_device_login' => 'محاولة دخول من جهاز محظور 🚨',
      'support_ticket_updated' => 'تحديث من الدعم الفني 🛠️',
      _ => 'تنبيه أمني من خدووم 🔐',
    };
    await _plugin.show(
      id: idFor('security_device_${item['id']}'),
      title: title,
      body:
          item['summary']?.toString() ??
          'تم تسجيل دخول جهاز جديد إلى حساب المؤسسة',
      notificationDetails: const NotificationDetails(
        android: AndroidNotificationDetails(
          'khdoom_security_alerts_v1',
          'تنبيهات خدووم الأمنية',
          channelDescription: 'إشعارات صوتية للأحداث الأمنية المهمة في المؤسسة',
          importance: Importance.max,
          priority: Priority.max,
          playSound: true,
          enableVibration: true,
          audioAttributesUsage: AudioAttributesUsage.notification,
          category: AndroidNotificationCategory.alarm,
        ),
      ),
      payload: 'security_${item['id']}',
    );
  }

  static Future<void> scheduleAppointment(
    Map<String, dynamic> item, {
    BranchPreferences? scope,
  }) async {
    final prefs = scope ?? await BranchPreferences.getInstance();
    if (item['status'] == 'completed') return;
    final reminder = DateTime.tryParse(item['reminderDate']?.toString() ?? '');
    if (reminder == null || !reminder.isAfter(DateTime.now())) return;
    await requestPermission();
    await _plugin.zonedSchedule(
      id: idFor(prefs.keyFor('alert_appointment_${item['id']}')),
      title: '${prefs.branchName} — تذكير ${item['type'] ?? 'موعد'}',
      body:
          (item['title']?.toString() ?? 'لديك موعد قريب') +
          ((item['customer']?.toString() ?? '').isEmpty
              ? ''
              : ' â€” ' + item['customer'].toString()),
      scheduledDate: tz.TZDateTime.from(reminder, tz.local),
      notificationDetails: const NotificationDetails(
        android: AndroidNotificationDetails(
          'khdoom_audible_alerts_v2',
          'تنبيهات خدووم الصوتية',
          channelDescription: 'تنبيهات المواعيد والطلبات بصوت واهتزاز',
          importance: Importance.max,
          priority: Priority.max,
          playSound: true,
          enableVibration: true,
          audioAttributesUsage: AudioAttributesUsage.alarm,
          category: AndroidNotificationCategory.alarm,
        ),
      ),
      androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
      payload: item['id']?.toString(),
    );
  }

  static Future<void> _scheduleExpirySeries({
    required String key,
    required String title,
    required dynamic rawDate,
  }) async {
    final expiry = DateTime.tryParse(rawDate?.toString() ?? '');
    if (expiry == null) return;
    for (final days in const [30, 7, 1]) {
      final notificationId = idFor('expiry_' + key + '_' + days.toString());
      final scheduled = tz.TZDateTime(
        tz.local,
        expiry.year,
        expiry.month,
        expiry.day,
        9,
      ).subtract(Duration(days: days));
      if (!scheduled.isAfter(tz.TZDateTime.now(tz.local))) continue;
      await _plugin.zonedSchedule(
        id: notificationId,
        title: 'تنبيه انتهاء: ' + title,
        body: days == 1
            ? 'ينتهي غدًا. راجع البيانات واتخذ الإجراء المطلوب.'
            : 'متبقي ' + days.toString() + ' يومًا على الانتهاء.',
        scheduledDate: scheduled,
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'khdoom_audible_alerts_v2',
            'تنبيهات خدووم الصوتية',
            channelDescription: 'تنبيهات وثائق المؤسسة والمركبات والموظفين والفواتير بصوت واهتزاز',
            importance: Importance.max,
            priority: Priority.max,
            playSound: true,
            enableVibration: true,
            audioAttributesUsage: AudioAttributesUsage.alarm,
            category: AndroidNotificationCategory.alarm,
          ),
        ),
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
        payload: key,
      );
    }
  }

  static List<Map<String, dynamic>> _storedList(
    BranchPreferences prefs,
    String key,
  ) {
    final saved = prefs.getString(key);
    if (saved == null || saved.isEmpty) return [];
    try {
      return (jsonDecode(saved) as List<dynamic>)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> _syncQueue = Future<void>.value();

  static Future<void> syncStoredAlerts() {
    final next = _syncQueue.then((_) => _syncStoredAlertsNow());
    _syncQueue = next.catchError((Object _) {});
    return next;
  }

  static Future<void> _syncStoredAlertsNow() async {
    final prefs = await BranchPreferences.getInstance();
    final enabled = prefs.getBool('settings_notifications') ?? true;
    await _plugin.cancelAll();
    if (!enabled) return;
    await requestPermission();

    final branches = prefs.getString('session_user_type') == 'employee'
        ? [
            {'id': prefs.branchId, 'name': prefs.branchName},
          ]
        : BranchPreferences.branches(prefs.raw);
    for (final branch in branches) {
      await _syncBranchAlerts(BranchPreferences(prefs.raw, branch['id']!));
    }
  }

  static Future<void> _syncBranchAlerts(BranchPreferences prefs) async {
    Future<void> expiry({
      required String key,
      required String title,
      required dynamic rawDate,
    }) => _scheduleExpirySeries(
      key: prefs.keyFor('alert_$key'),
      title: '${prefs.branchName} â€” $title',
      rawDate: rawDate,
    );

    if (prefs.getBool('settings_appointment_reminders') ?? true) {
      for (final item in _storedList(prefs, 'business_appointments_requests')) {
        await scheduleAppointment(item, scope: prefs);
      }
    }

    for (final vehicle in _storedList(prefs, 'business_vehicles')) {
      final id = vehicle['id']?.toString() ?? vehicle['name']?.toString() ?? '';
      final name = vehicle['name']?.toString() ?? 'المركبة';
      await expiry(
        key: 'vehicle_' + id + '_registration',
        title: 'استمارة ' + name,
        rawDate: vehicle['registration'],
      );
      await expiry(
        key: 'vehicle_' + id + '_inspection',
        title: 'فحص ' + name,
        rawDate: vehicle['inspection'],
      );
      await expiry(
        key: 'vehicle_' + id + '_insurance',
        title: 'تأمين ' + name,
        rawDate: vehicle['insurance'],
      );
    }

    for (final employee in _storedList(prefs, 'employee_alert_records')) {
      if (employee['archived'] == true) continue;
      final id =
          employee['id']?.toString() ?? employee['name']?.toString() ?? '';
      final name = employee['name']?.toString() ?? 'الموظف';
      await expiry(
        key: 'employee_' + id + '_iqama',
        title: 'إقامة ' + name,
        rawDate: employee['iqama'],
      );
      await expiry(
        key: 'employee_' + id + '_contract',
        title: 'عقد ' + name,
        rawDate: employee['contract'],
      );
      await expiry(
        key: 'employee_' + id + '_insurance',
        title: 'تأمين ' + name,
        rawDate: employee['insurance'],
      );
    }

    final organizationRecords = _storedList(
      prefs,
      'organization_alert_records',
    );
    if (prefs.containsKey('organization_alert_records')) {
      for (final record in organizationRecords) {
        final id =
            record['id']?.toString() ?? record['title']?.toString() ?? '';
        await expiry(
          key: 'organization_$id',
          title: record['title']?.toString() ?? 'مستند المؤسسة',
          rawDate: record['date'],
        );
      }
    } else {
      for (final title in const [
        'الرخصة البلدية',
        'السجل التجاري',
        'اشتراك قوى',
        'حماية الأجور – مدد',
        'شهادة الدفاع المدني',
      ]) {
        await expiry(
          key: 'organization_$title',
          title: title,
          rawDate: prefs.getString('alert_$title'),
        );
      }
    }

    for (final bill in _storedList(prefs, 'electricity_bills')) {
      if (bill['paid'] == true) continue;
      final id = bill['id']?.toString() ?? bill['month']?.toString() ?? '';
      await expiry(
        key: 'electricity_' + id,
        title: 'فاتورة الكهرباء ' + (bill['month']?.toString() ?? ''),
        rawDate: bill['dueDate'],
      );
    }
  }

  static Future<void> cancelAppointment(
    dynamic id, {
    BranchPreferences? scope,
  }) async {
    final prefs = scope ?? await BranchPreferences.getInstance();
    await _plugin.cancel(id: idFor(prefs.keyFor('alert_appointment_$id')));
  }
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await KhdoomNotifications.initialize();
  runApp(const KhdoomApp());
}

class KhdoomApp extends StatelessWidget {
  const KhdoomApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'خدووم',
      // اترك Flutter يختار خط النظام حتى يعمل تشكيل العربية بشكل صحيح على Android.
      // Android's sans-serif family contains Arabic shaping on supported devices and avoids broken fallback glyphs.
      theme: ThemeData(useMaterial3: true, fontFamily: 'sans-serif'),
      home: const AppLaunchGate(),
    );
  }
}

class AppLaunchGate extends StatefulWidget {
  const AppLaunchGate({super.key});

  @override
  State<AppLaunchGate> createState() => _AppLaunchGateState();
}

class _AppLaunchGateState extends State<AppLaunchGate> {
  static const _secureStorage = FlutterSecureStorage();
  bool? _requiresPin;
  bool _accountCreated = false;
  bool _hasSession = false;

  @override
  void initState() {
    super.initState();
    _checkLock();
  }

  Future<void> _checkLock() async {
    final prefs = await BranchPreferences.getInstance();
    final enabled = prefs.getBool('security_app_lock') ?? false;
    final accountCreated = prefs.getBool('local_account_created') ?? false;
    var sessionType = prefs.getString('session_user_type');
    final loggedOutBefore = prefs.getBool('has_logged_out_once') ?? false;
    if (accountCreated && sessionType == null && !loggedOutBefore) {
      sessionType = 'admin';
      await prefs.setString('session_user_type', 'admin');
    }
    final savedPin = await _secureStorage.read(key: 'security_app_pin');
    if (!mounted) return;
    setState(() {
      _accountCreated = accountCreated;
      _hasSession = sessionType != null;
      _requiresPin = enabled && savedPin != null && _hasSession;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_requiresPin == null) {
      return const Scaffold(
        backgroundColor: Color(0xFF0B1020),
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_requiresPin!) {
      return AppLockPage(
        onUnlocked: () => setState(() => _requiresPin = false),
      );
    }
    if (!_accountCreated) return const HomePage();
    return _hasSession ? const DashboardPage() : const LoginPage();
  }
}

class AppLockPage extends StatefulWidget {
  final VoidCallback onUnlocked;

  const AppLockPage({super.key, required this.onUnlocked});

  @override
  State<AppLockPage> createState() => _AppLockPageState();
}

class _AppLockPageState extends State<AppLockPage> {
  static const _secureStorage = FlutterSecureStorage();
  final _pinController = TextEditingController();
  String? _errorText;

  Future<void> _unlock() async {
    final savedPin = await _secureStorage.read(key: 'security_app_pin');
    if (_pinController.text == savedPin) {
      widget.onUnlocked();
      return;
    }
    setState(() {
      _errorText = 'رمز القفل غير صحيح';
      _pinController.clear();
    });
  }

  @override
  void dispose() {
    _pinController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(28),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Column(
                  children: [
                    Container(
                      width: 92,
                      height: 92,
                      decoration: const BoxDecoration(
                        color: Color(0xFF172554),
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(
                        Icons.lock_outline,
                        color: Color(0xFF7DD3FC),
                        size: 46,
                      ),
                    ),
                    const SizedBox(height: 20),
                    const Text(
                      'خدووم مقفل',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 26,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'أدخل رمز PIN للمتابعة',
                      style: TextStyle(color: Colors.white60),
                    ),
                    const SizedBox(height: 22),
                    TextField(
                      controller: _pinController,
                      autofocus: true,
                      obscureText: true,
                      maxLength: 6,
                      keyboardType: TextInputType.number,
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _unlock(),
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 22,
                        letterSpacing: 8,
                      ),
                      textAlign: TextAlign.center,
                      decoration: InputDecoration(
                        hintText: 'â€¢â€¢â€¢â€¢',
                        errorText: _errorText,
                        counterText: '',
                        hintStyle: const TextStyle(
                          color: Colors.white24,
                          letterSpacing: 8,
                        ),
                        filled: true,
                        fillColor: const Color(0xFF172554),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: BorderSide.none,
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(
                            color: Color(0xFF38BDF8),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: _unlock,
                        icon: const Icon(Icons.lock_open),
                        label: const Text('فتح التطبيق'),
                        style: FilledButton.styleFrom(
                          backgroundColor: const Color(0xFF0284C7),
                          padding: const EdgeInsets.symmetric(vertical: 16),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class ForgotUsernameDialog extends StatefulWidget {
  const ForgotUsernameDialog({super.key});

  @override
  State<ForgotUsernameDialog> createState() => _ForgotUsernameDialogState();
}

class _ForgotUsernameDialogState extends State<ForgotUsernameDialog> {
  final _phoneController = TextEditingController();

  @override
  void dispose() {
    _phoneController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: const Text(
      'نسيت اسم المستخدم',
      style: TextStyle(color: Colors.white),
    ),
    content: TextField(
      controller: _phoneController,
      keyboardType: TextInputType.phone,
      style: const TextStyle(color: Colors.white),
      decoration: _recoveryDecoration(
        'رقم الجوال المسجل',
        Icons.phone_outlined,
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('إلغاء'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, _phoneController.text.trim()),
        child: const Text('استرجاع'),
      ),
    ],
  );
}

class ForgotPasswordDialog extends StatefulWidget {
  const ForgotPasswordDialog({super.key});

  @override
  State<ForgotPasswordDialog> createState() => _ForgotPasswordDialogState();
}

class _ForgotPasswordDialogState extends State<ForgotPasswordDialog> {
  final _usernameController = TextEditingController();
  final _phoneController = TextEditingController();
  final _newPasswordController = TextEditingController();

  @override
  void dispose() {
    _usernameController.dispose();
    _phoneController.dispose();
    _newPasswordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: const Text(
      'تعيين كلمة مرور جديدة',
      style: TextStyle(color: Colors.white),
    ),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _usernameController,
            style: const TextStyle(color: Colors.white),
            decoration: _recoveryDecoration(
              'اسم المستخدم',
              Icons.person_outline,
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _phoneController,
            keyboardType: TextInputType.phone,
            style: const TextStyle(color: Colors.white),
            decoration: _recoveryDecoration(
              'رقم الجوال المسجل',
              Icons.phone_outlined,
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _newPasswordController,
            obscureText: true,
            style: const TextStyle(color: Colors.white),
            decoration: _recoveryDecoration(
              'كلمة المرور الجديدة (8 خانات على الأقل)',
              Icons.password_outlined,
            ),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('إلغاء'),
      ),
      FilledButton(
        onPressed: () {
          if (_newPasswordController.text.length < 8) {
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text('كلمة المرور يجب أن تكون 8 خانات على الأقل'),
              ),
            );
            return;
          }
          Navigator.pop(context, {
            'username': _usernameController.text.trim().toLowerCase(),
            'phone': _phoneController.text.trim(),
            'password': _newPasswordController.text,
          });
        },
        child: const Text('حفظ الجديدة'),
      ),
    ],
  );
}

InputDecoration _recoveryDecoration(String label, IconData icon) {
  return InputDecoration(
    labelText: label,
    labelStyle: const TextStyle(color: Colors.white70),
    prefixIcon: Icon(icon, color: const Color(0xFF38BDF8)),
    filled: true,
    fillColor: const Color(0xFF111B35),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(14),
      borderSide: BorderSide.none,
    ),
  );
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  static const _secureStorage = FlutterSecureStorage();
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _hidePassword = true;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadRememberedUsername();
  }

  Future<void> _loadRememberedUsername() async {
    final prefs = await BranchPreferences.getInstance();
    final remembered = prefs.getString('remembered_login_username');
    final hasAccount = prefs.getBool('local_account_created') ?? false;
    if (!mounted) return;
    _usernameController.text = (remembered != null && remembered.isNotEmpty)
        ? remembered
        : (hasAccount
              ? (prefs.getString('admin_login_username') ?? 'admin')
              : '');
  }

  Future<void> _login() async {
    final username = _usernameController.text.trim().toLowerCase();
    final password = _passwordController.text;
    if (username.isEmpty || password.isEmpty) {
      setState(() => _error = 'أدخل اسم المستخدم وكلمة المرور');
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    final prefs = await BranchPreferences.getInstance();
    final adminPhone = (prefs.getString('account_phone') ?? '').toLowerCase();
    final adminEmail = (prefs.getString('account_email') ?? '').toLowerCase();
    final adminUsername = (prefs.getString('admin_login_username') ?? 'admin')
        .toLowerCase();
    final adminPassword = await _secureStorage.read(
      key: 'admin_account_password',
    );
    final isAdminName =
        username == adminUsername ||
        username == adminPhone ||
        username == adminEmail;
    if (isAdminName && adminPassword == password) {
      await prefs.setString('remembered_login_username', username);
      await prefs.setString('session_user_type', 'admin');
      await prefs.remove('session_employee_id');
      await prefs.remove('session_employee_name');
      await prefs.remove('session_employee_permissions');
      await _refreshCloudToken(username, password, prefs);
      if (!mounted) return;
      Navigator.pushAndRemoveUntil(
        context,
        MaterialPageRoute(builder: (_) => const DashboardPage()),
        (_) => false,
      );
      return;
    }

    {
      final employees = prefs.allEmployees();
      for (final employee in employees) {
        final employeeUsername = (employee['username'] as String? ?? '')
            .trim()
            .toLowerCase();
        if (employeeUsername != username || employee['active'] != true) {
          continue;
        }
        final id = employee['id'].toString();
        final savedPassword = await _secureStorage.read(
          key: 'employee_password_$id',
        );
        if (savedPassword == password) {
          await prefs.setString('remembered_login_username', username);
          await prefs.setString('session_user_type', 'employee');
          await prefs.setString('session_employee_id', id);
          await prefs.setString(
            'session_employee_name',
            employee['name'].toString(),
          );
          await prefs.setString(
            'session_employee_permissions',
            jsonEncode(employee['permissions'] as Map? ?? {}),
          );
          // Never reuse an administrator's cloud token for a local employee.
          await _secureStorage.delete(key: 'cloud_session_token');
          final employeeApi = KhdoomCloudApi(
            scope: prefs,
            baseUrl:
                prefs.getString('cloud_api_url') ??
                'https://khdoom-api.onrender.com',
          );
          try {
            final device = await khdoomDeviceIdentity();
            final login = await employeeApi.login(
              username,
              password,
              deviceId: device['id']!,
              deviceName: device['name']!,
            );
            final user = Map<String, dynamic>.from(login['user'] as Map);
            await prefs.setString(
              'session_employee_permissions',
              jsonEncode(user['permissions'] ?? {}),
            );
            await _secureStorage.write(
              key: 'cloud_session_token',
              value: login['token'].toString(),
            );
          } on CloudApiException catch (error) {
            if (error.statusCode == 401 || error.statusCode == 403) {
              await prefs.remove('session_user_type');
              if (mounted)
                setState(() {
                  _loading = false;
                  _error = error.message;
                });
              return;
            }
          } catch (_) {
            // Offline login keeps local records, without a cloud token.
          } finally {
            employeeApi.close();
          }
          if (!mounted) return;
          Navigator.pushAndRemoveUntil(
            context,
            MaterialPageRoute(builder: (_) => const DashboardPage()),
            (_) => false,
          );
          return;
        }
      }
    }
    final cloudApi = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    try {
      final device = await khdoomDeviceIdentity();
      final loginResult = await cloudApi.login(
        username,
        password,
        deviceId: device['id']!,
        deviceName: device['name']!,
      );
      final organization = await cloudApi.organization();
      final user = Map<String, dynamic>.from(loginResult['user'] as Map? ?? {});
      final cloudToken = loginResult['token']?.toString() ?? '';
      final isEmployee = user['role']?.toString() == 'employee';
      final permissions = Map<String, dynamic>.from(
        user['permissions'] as Map? ?? {},
      );
      await Future.wait([
        prefs.setBool('local_account_created', true),
        prefs.setString('remembered_login_username', username),
        prefs.setString('session_user_type', isEmployee ? 'employee' : 'admin'),
        prefs.setString(
          'subscription_package',
          loginResult['package']?.toString() ?? 'free',
        ),
        prefs.setString(
          'account_business_name',
          organization['name']?.toString() ?? '',
        ),
        prefs.setString(
          'account_phone',
          organization['phone']?.toString() ?? '',
        ),
        if (isEmployee) ...[
          prefs.setString('session_employee_id', user['id'].toString()),
          prefs.setString(
            'session_employee_name',
            user['name']?.toString() ?? username,
          ),
          prefs.setString(
            'session_employee_permissions',
            jsonEncode(permissions),
          ),
        ] else ...[
          prefs.setString('admin_login_username', username),
          prefs.setString('account_name', user['name']?.toString() ?? username),
          prefs.remove('session_employee_id'),
          prefs.remove('session_employee_name'),
          prefs.remove('session_employee_permissions'),
          _secureStorage.write(key: 'admin_account_password', value: password),
        ],
        if (cloudToken.isNotEmpty)
          _secureStorage.write(key: 'cloud_session_token', value: cloudToken),
      ]);
      if (!mounted) return;
      Navigator.pushAndRemoveUntil(
        context,
        MaterialPageRoute(builder: (_) => const DashboardPage()),
        (_) => false,
      );
      return;
    } catch (_) {
      // تعرض رسالة الدخول المعتادة إذا لم ينجح الدخول المحلي أو السحابي.
    } finally {
      cloudApi.close();
    }
    if (!mounted) return;
    setState(() {
      _loading = false;
      _error = 'اسم المستخدم أو كلمة المرور غير صحيحة';
    });
  }

  Future<void> _refreshCloudToken(
    String username,
    String password,
    BranchPreferences prefs,
  ) async {
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    final device = await khdoomDeviceIdentity();
    try {
      final result = await api.login(
        username,
        password,
        deviceId: device['id']!,
        deviceName: device['name']!,
      );
      final cloudToken = result['token']?.toString();
      if (cloudToken != null && cloudToken.isNotEmpty) {
        await _secureStorage.write(
          key: 'cloud_session_token',
          value: cloudToken,
        );
      }
    } on CloudApiException {
      if (password.length >= 8) {
        try {
          final result = await api.register({
            'name': prefs.getString('account_name') ?? username,
            'username': username,
            'phone': prefs.getString('account_phone') ?? '',
            'email': prefs.getString('account_email') ?? '',
            'password': password,
            'organizationName':
                prefs.getString('account_business_name') ?? 'مؤسسة خدووم',
            'deviceId': device['id'],
            'deviceName': device['name'],
          });
          final cloudToken = result['token']?.toString();
          if (cloudToken != null && cloudToken.isNotEmpty) {
            await _secureStorage.write(
              key: 'cloud_session_token',
              value: cloudToken,
            );
          }
        } catch (_) {
          // يمكن متابعة استخدام الحساب المحلي إذا تعذرت المزامنة.
        }
      }
    } catch (_) {
      // يبقى الدخول المحلي متاحًا عند انقطاع الخادم.
    } finally {
      api.close();
    }
  }

  Future<void> _forgotUsername() async {
    final phone = await showDialog<String>(
      context: context,
      builder: (_) => const ForgotUsernameDialog(),
    );
    if (phone == null || phone.isEmpty || !mounted) return;
    final prefs = await BranchPreferences.getInstance();
    final usernames = <String>[];
    if ((prefs.getString('account_phone') ?? '').trim() == phone) {
      usernames.add(
        'المدير: ${prefs.getString('admin_login_username') ?? 'admin'}',
      );
    }
    final savedEmployees = prefs.getString('business_employees');
    if (savedEmployees != null) {
      for (final item in jsonDecode(savedEmployees) as List) {
        final employee = Map<String, dynamic>.from(item as Map);
        if ((employee['phone'] ?? '').toString().trim() == phone &&
            employee['active'] == true) {
          final username = (employee['username'] ?? '').toString();
          if (username.isNotEmpty) {
            usernames.add('${employee['name']}: $username');
          }
        }
      }
    }
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          usernames.isEmpty ? 'لم يتم العثور على حساب' : 'اسم المستخدم',
        ),
        content: Text(
          usernames.isEmpty
              ? 'رقم الجوال غير مطابق لأي حساب نشط.'
              : usernames.join('\n'),
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('حسنًا'),
          ),
        ],
      ),
    );
  }

  Future<void> _forgotPassword() async {
    final data = await showDialog<Map<String, String>>(
      context: context,
      builder: (_) => const ForgotPasswordDialog(),
    );
    if (data == null || !mounted) return;
    if ((data['password'] ?? '').length < 8) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('كلمة المرور يجب أن تكون 8 خانات على الأقل'),
        ),
      );
      return;
    }
    final prefs = await BranchPreferences.getInstance();
    final username = data['username']!;
    final phone = data['phone']!;
    var changed = false;
    final adminPhone = (prefs.getString('account_phone') ?? '').trim();
    final adminEmail = (prefs.getString('account_email') ?? '')
        .trim()
        .toLowerCase();
    final adminUsername = (prefs.getString('admin_login_username') ?? 'admin')
        .trim()
        .toLowerCase();
    if (phone == adminPhone &&
        (username == adminUsername ||
            username == adminPhone.toLowerCase() ||
            username == adminEmail)) {
      await _secureStorage.write(
        key: 'admin_account_password',
        value: data['password'],
      );
      changed = true;
    } else {
      final savedEmployees = prefs.getString('business_employees');
      if (savedEmployees != null) {
        for (final item in jsonDecode(savedEmployees) as List) {
          final employee = Map<String, dynamic>.from(item as Map);
          if ((employee['username'] ?? '').toString().toLowerCase() ==
                  username &&
              (employee['phone'] ?? '').toString().trim() == phone &&
              employee['active'] == true) {
            await _secureStorage.write(
              key: 'employee_password_${employee['id']}',
              value: data['password'],
            );
            changed = true;
            break;
          }
        }
      }
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          changed
              ? 'تم تغيير كلمة المرور، يمكنك تسجيل الدخول الآن'
              : 'اسم المستخدم أو رقم الجوال غير مطابق',
        ),
      ),
    );
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF020B22),
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          foregroundColor: Colors.white,
          title: const Text('تسجيل الدخول'),
        ),
        body: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 430),
              child: Column(
                children: [
                  Image.asset(
                    'assets/images/khdoom_logo.png',
                    width: 150,
                    height: 150,
                  ),
                  const SizedBox(height: 20),
                  TextField(
                    controller: _usernameController,
                    style: const TextStyle(color: Colors.white),
                    textInputAction: TextInputAction.next,
                    decoration: _loginDecoration(
                      'اسم المستخدم أو جوال المدير',
                      Icons.person_outline,
                    ),
                  ),
                  const SizedBox(height: 14),
                  TextField(
                    controller: _passwordController,
                    obscureText: _hidePassword,
                    style: const TextStyle(color: Colors.white),
                    onSubmitted: (_) => _login(),
                    decoration:
                        _loginDecoration(
                          'كلمة المرور',
                          Icons.lock_outline,
                        ).copyWith(
                          suffixIcon: IconButton(
                            onPressed: () =>
                                setState(() => _hidePassword = !_hidePassword),
                            icon: Icon(
                              _hidePassword
                                  ? Icons.visibility_outlined
                                  : Icons.visibility_off_outlined,
                              color: Colors.white60,
                            ),
                          ),
                        ),
                  ),
                  const SizedBox(height: 4),
                  Wrap(
                    alignment: WrapAlignment.center,
                    spacing: 8,
                    children: [
                      TextButton(
                        onPressed: _forgotPassword,
                        child: const Text('نسيت كلمة المرور؟'),
                      ),
                      TextButton(
                        onPressed: _forgotUsername,
                        child: const Text('نسيت اسم المستخدم؟'),
                      ),
                    ],
                  ),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Text(
                      _error!,
                      style: const TextStyle(color: Colors.redAccent),
                    ),
                  ],
                  const SizedBox(height: 22),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      onPressed: _loading ? null : _login,
                      icon: _loading
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.login),
                      label: const Text('دخول'),
                    ),
                  ),
                  const SizedBox(height: 12),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _loading
                          ? null
                          : () => Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => const CreateAccountPage(),
                              ),
                            ),
                      icon: const Icon(Icons.person_add_alt_1),
                      label: const Text('إنشاء حساب جديد'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white,
                        side: const BorderSide(color: Color(0xFF38BDF8)),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  InputDecoration _loginDecoration(String label, IconData icon) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white70),
      prefixIcon: Icon(icon, color: const Color(0xFF38BDF8)),
      filled: true,
      fillColor: const Color(0xFF111B35),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
    );
  }
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  void _createAccount(BuildContext context) {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const CreateAccountPage()),
    );
  }

  void _login(BuildContext context) {
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const LoginPage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF020B22),
        body: Stack(
          children: [
            const Positioned(
              top: 230,
              left: -120,
              right: -120,
              child: SizedBox(
                height: 300,
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(
                      colors: [Color(0x3322D3EE), Color(0x00020B22)],
                    ),
                  ),
                ),
              ),
            ),
            SafeArea(
              child: LayoutBuilder(
                builder: (context, constraints) {
                  final compact = constraints.maxWidth < 380;
                  return SingleChildScrollView(
                    padding: EdgeInsets.fromLTRB(
                      compact ? 16 : 22,
                      14,
                      compact ? 16 : 22,
                      24,
                    ),
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 560),
                        child: Column(
                          children: [
                            Image.asset(
                              'assets/images/khdoom_logo.png',
                              width: compact ? 176 : 210,
                              height: compact ? 176 : 210,
                            ),
                            const Text(
                              'موظفو الذكاء الاصطناعي لمؤسستك',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                color: Color(0xFF7DD3FC),
                                fontSize: 16,
                              ),
                            ),
                            SizedBox(height: compact ? 28 : 40),
                            RichText(
                              textAlign: TextAlign.center,
                              text: const TextSpan(
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 27,
                                  fontWeight: FontWeight.bold,
                                ),
                                children: [
                                  TextSpan(text: 'مرحبًا بك في '),
                                  TextSpan(
                                    text: 'خدووم',
                                    style: TextStyle(color: Color(0xFF22D3EE)),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(height: 12),
                            const Text(
                              'منصة ذكية تساعد مؤسستك على تنظيم\nأعمالك اليومية وإدارتها بسهولة',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                color: Colors.white70,
                                fontSize: 16,
                                height: 1.6,
                              ),
                            ),
                            SizedBox(height: compact ? 24 : 32),
                            const Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Expanded(
                                  child: _WelcomeFeature(
                                    icon: Icons.smart_toy_outlined,
                                    title: 'موظفو AI',
                                    subtitle: 'يخدمون عملاءك على مدار الساعة',
                                  ),
                                ),
                                SizedBox(width: 8),
                                Expanded(
                                  child: _WelcomeFeature(
                                    icon: Icons.notifications_active_outlined,
                                    title: 'تنبيهات ذكية',
                                    subtitle:
                                        'لا تفوّت التزامًا أو موعدًا مهمًا',
                                  ),
                                ),
                                SizedBox(width: 8),
                                Expanded(
                                  child: _WelcomeFeature(
                                    icon: Icons.bar_chart_rounded,
                                    title: 'إدارة أسهل',
                                    subtitle: 'كل أعمالك في مكان واحد',
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 32),
                            SizedBox(
                              width: double.infinity,
                              child: FilledButton(
                                onPressed: () => _createAccount(context),
                                style: FilledButton.styleFrom(
                                  backgroundColor: const Color(0xFF0788FF),
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 17,
                                  ),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(14),
                                  ),
                                ),
                                child: const Text(
                                  'ابدأ الآن',
                                  style: TextStyle(
                                    fontSize: 20,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(height: 12),
                            SizedBox(
                              width: double.infinity,
                              child: OutlinedButton.icon(
                                onPressed: () => _login(context),
                                icon: const Icon(Icons.login_rounded),
                                label: const Text(
                                  'تسجيل الدخول',
                                  style: TextStyle(fontSize: 18),
                                ),
                                style: OutlinedButton.styleFrom(
                                  foregroundColor: Colors.white,
                                  side: const BorderSide(
                                    color: Color(0xFF1E4D82),
                                  ),
                                  padding: const EdgeInsets.symmetric(
                                    vertical: 15,
                                  ),
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(14),
                                  ),
                                ),
                              ),
                            ),
                            const SizedBox(height: 10),
                            Wrap(
                              alignment: WrapAlignment.center,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                const Text(
                                  'جديد في خدووم؟',
                                  style: TextStyle(color: Colors.white60),
                                ),
                                TextButton(
                                  onPressed: () => _createAccount(context),
                                  child: const Text('إنشاء حساب'),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _WelcomeFeature extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;

  const _WelcomeFeature({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: 62,
          height: 62,
          decoration: BoxDecoration(
            color: const Color(0xFF071A3D),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: const Color(0xFF0E3B6C)),
            boxShadow: const [
              BoxShadow(color: Color(0x3322D3EE), blurRadius: 16),
            ],
          ),
          child: Icon(icon, color: const Color(0xFF18C7FF), size: 34),
        ),
        const SizedBox(height: 9),
        Text(
          title,
          maxLines: 1,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 14,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 5),
        Text(
          subtitle,
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: Colors.white60,
            fontSize: 11,
            height: 1.35,
          ),
        ),
      ],
    );
  }
}

class CreateAccountPage extends StatefulWidget {
  const CreateAccountPage({super.key});

  @override
  State<CreateAccountPage> createState() => _CreateAccountPageState();
}

class _CreateAccountPageState extends State<CreateAccountPage> {
  final _formKey = GlobalKey<FormState>();
  final _nameController = TextEditingController();
  final _phoneController = TextEditingController();
  final _emailController = TextEditingController();
  final _businessNameController = TextEditingController();
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();
  bool _hidePassword = true;
  bool _hideConfirmPassword = true;
  bool _acceptedTerms = false;
  bool _isSaving = false;
  String _entityType = 'institution';
  int _registrationStep = 1;

  Future<void> _createAccount() async {
    if (!_formKey.currentState!.validate()) return;
    if (!_acceptedTerms) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('وافق على الشروط وسياسة الخصوصية أولًا.')),
      );
      return;
    }

    setState(() => _isSaving = true);
    final prefs = await BranchPreferences.getInstance();
    var package = 'free';
    try {
      final api = KhdoomCloudApi(
        scope: prefs,
        baseUrl:
            prefs.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      );
      final device = await khdoomDeviceIdentity();
      final result = await api.register({
        'name': _nameController.text.trim(),
        'username': _usernameController.text.trim(),
        'phone': _phoneController.text.trim(),
        'email': _emailController.text.trim(),
        'password': _passwordController.text,
        'organizationName': _businessNameController.text.trim(),
        'entityType': _entityType,
        'deviceId': device['id'],
        'deviceName': device['name'],
      });
      package = result['package']?.toString() ?? 'free';
      final cloudToken = result['token']?.toString();
      if (cloudToken != null && cloudToken.isNotEmpty) {
        const storage = FlutterSecureStorage();
        await storage.write(key: 'cloud_session_token', value: cloudToken);
      }
      api.close();
    } catch (error) {
      // يبقى التسجيل المجاني متاحًا محليًا عند تعذر اتصال الخادم.
    }
    const secureStorage = FlutterSecureStorage();
    await Future.wait([
      prefs.setBool('local_account_created', true),
      prefs.setString('session_user_type', 'admin'),
      prefs.setString(
        'admin_login_username',
        _usernameController.text.trim().toLowerCase(),
      ),
      prefs.setString('subscription_package', package),
      prefs.setBool('has_logged_out_once', false),
      prefs.setString('account_name', _nameController.text.trim()),
      prefs.setString('account_phone', _phoneController.text.trim()),
      prefs.setString('account_email', _emailController.text.trim()),
      prefs.setString(
        'account_business_name',
        _businessNameController.text.trim(),
      ),
      secureStorage.write(
        key: 'admin_account_password',
        value: _passwordController.text,
      ),
    ]);
    if (!mounted) return;
    setState(() => _isSaving = false);
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(
        builder: (_) => const EducationPage(isOnboarding: true),
      ),
      (route) => route.isFirst,
    );
  }

  @override
  void dispose() {
    _nameController.dispose();
    _phoneController.dispose();
    _emailController.dispose();
    _businessNameController.dispose();
    _usernameController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF0B1020),
          foregroundColor: Colors.white,
          elevation: 0,
          title: const Text('إنشاء حساب'),
        ),
        body: SafeArea(
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
              children: [
                const Icon(
                  Icons.hub_outlined,
                  color: Color(0xFF38BDF8),
                  size: 58,
                ),
                const SizedBox(height: 10),
                const Text(
                  'أنشئ حسابك في خدووم',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 26,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 6),
                const Text(
                  'أدخل بياناتك الأساسية للبدء بإدارة مؤسستك وموظفي AI.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Colors.white60, height: 1.5),
                ),
                Card(
                  color: const Color(0xFF17233D),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Text(
                      'الخطوة $_registrationStep من 6',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                _accountField(
                  controller: _nameController,
                  label: 'الاسم الكامل',
                  icon: Icons.person_outline,
                  textInputAction: TextInputAction.next,
                  validator: (value) => value == null || value.trim().length < 3
                      ? 'اكتب الاسم الكامل'
                      : null,
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _phoneController,
                  label: 'رقم الجوال',
                  hint: '05XXXXXXXX',
                  icon: Icons.phone_android,
                  keyboardType: TextInputType.phone,
                  textInputAction: TextInputAction.next,
                  validator: (value) {
                    final phone = (value ?? '').replaceAll(RegExp(r'\D'), '');
                    return phone.length < 9 ? 'أدخل رقم جوال صحيحًا' : null;
                  },
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _emailController,
                  label: 'البريد الإلكتروني',
                  hint: 'name@example.com',
                  icon: Icons.email_outlined,
                  keyboardType: TextInputType.emailAddress,
                  textInputAction: TextInputAction.next,
                  validator: (value) {
                    final email = value?.trim() ?? '';
                    return !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
                            .hasMatch(email)
                        ? 'أدخل بريدًا إلكترونيًا صحيحًا'
                        : null;
                  },
                ),
                const SizedBox(height: 13),
                DropdownButtonFormField<String>(
                  value: _entityType,
                  decoration: const InputDecoration(
                    labelText: 'نوع الكيان',
                    prefixIcon: Icon(Icons.category_outlined),
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: 'institution',
                      child: Text('مؤسسة'),
                    ),
                    DropdownMenuItem(value: 'company', child: Text('شركة')),
                  ],
                  onChanged: _isSaving
                      ? null
                      : (value) => setState(
                          () => _entityType = value ?? 'institution',
                        ),
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _businessNameController,
                  label: 'اسم المؤسسة',
                  hint: 'مثال: مرآتك للزجاج',
                  icon: Icons.business_outlined,
                  textInputAction: TextInputAction.next,
                  validator: (value) => value == null || value.trim().isEmpty
                      ? 'اكتب اسم المؤسسة'
                      : null,
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _usernameController,
                  label: 'اسم المستخدم',
                  hint: 'مثال: حيدر بن عشوان',
                  icon: Icons.alternate_email,
                  textInputAction: TextInputAction.next,
                  validator: (value) {
                    final username = value?.trim() ?? '';
                    if (username.length < 3) {
                      return 'اكتب اسم مستخدم من 3 خانات على الأقل';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _passwordController,
                  label: 'كلمة المرور',
                  icon: Icons.lock_outline,
                  obscureText: _hidePassword,
                  textInputAction: TextInputAction.next,
                  suffixIcon: IconButton(
                    onPressed: () =>
                        setState(() => _hidePassword = !_hidePassword),
                    icon: Icon(
                      _hidePassword ? Icons.visibility_off : Icons.visibility,
                      color: Colors.white54,
                    ),
                  ),
                  validator: (value) => (value ?? '').length < 8
                      ? 'استخدم 8 أحرف على الأقل'
                      : null,
                ),
                const SizedBox(height: 13),
                _accountField(
                  controller: _confirmPasswordController,
                  label: 'تأكيد كلمة المرور',
                  icon: Icons.lock_reset,
                  obscureText: _hideConfirmPassword,
                  textInputAction: TextInputAction.done,
                  suffixIcon: IconButton(
                    onPressed: () => setState(
                      () => _hideConfirmPassword = !_hideConfirmPassword,
                    ),
                    icon: Icon(
                      _hideConfirmPassword
                          ? Icons.visibility_off
                          : Icons.visibility,
                      color: Colors.white54,
                    ),
                  ),
                  validator: (value) => value != _passwordController.text
                      ? 'كلمتا المرور غير متطابقتين'
                      : null,
                ),
                const SizedBox(height: 12),
                CheckboxListTile(
                  value: _acceptedTerms,
                  onChanged: (value) =>
                      setState(() => _acceptedTerms = value ?? false),
                  activeColor: const Color(0xFF0284C7),
                  checkColor: Colors.white,
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  title: const Text(
                    'أوافق على الشروط وسياسة الخصوصية',
                    style: TextStyle(color: Colors.white70, fontSize: 14),
                  ),
                ),
                const SizedBox(height: 10),
                FilledButton.icon(
                  onPressed: _isSaving ? null : _createAccount,
                  icon: _isSaving
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.person_add_alt_1),
                  label: Text(_isSaving ? 'جاري الإنشاء...' : 'إنشاء الحساب'),
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF0284C7),
                    disabledBackgroundColor: const Color(0xFF1E3A5F),
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    textStyle: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                const Text(
                  'هذه المرحلة تحفظ الملف الشخصي على جهازك فقط. سنربط إنشاء الحساب بالخادم لاحقًا، ولا يتم حفظ كلمة المرور محليًا.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Colors.white38,
                    fontSize: 12,
                    height: 1.45,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _accountField({
    required TextEditingController controller,
    required String label,
    required IconData icon,
    String? hint,
    TextInputType? keyboardType,
    TextInputAction? textInputAction,
    bool obscureText = false,
    Widget? suffixIcon,
    String? Function(String?)? validator,
  }) {
    return TextFormField(
      controller: controller,
      keyboardType: keyboardType,
      textInputAction: textInputAction,
      obscureText: obscureText,
      validator: validator,
      style: const TextStyle(color: Colors.white),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        labelStyle: const TextStyle(color: Colors.white70),
        hintStyle: const TextStyle(color: Colors.white38),
        prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
        suffixIcon: suffixIcon,
        filled: true,
        fillColor: const Color(0xFF172554),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide.none,
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: Color(0xFF38BDF8)),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: Color(0xFFEF4444)),
        ),
      ),
    );
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key});

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  final TextEditingController _assistantController = TextEditingController();
  String _assistantAnswer = '';
  List<Map<String, dynamic>> _assistantRecords = [];
  bool _assistantLocked = false;
  String _assistantLockMessage = 'الخدمة متوقفة مؤقتًا';
  VoidCallback? _assistantAction;
  String _businessName = '';
  String _welcomeName = '';
  String? _businessLogoPath;
  bool _isAdmin = true;
  Map<String, dynamic> _permissions = {};
  String _subscriptionPackage = 'free';
  bool _showHomeBranches = false;
  List<Map<String, String>> _vipAdvertisements = [];
  Timer? _adRotationTimer;
  Timer? _requestRefreshTimer;
  Timer? _sessionValidationTimer;
  bool _forcingCloudLogout = false;
  bool _dashboardRefreshInFlight = false;
  bool _sessionValidationInFlight = false;
  int _currentAdIndex = 0;
  int _activeReminderCount = 0;
  int _securityAlertCount = 0;
  bool _alertsSynced = false;

  @override
  void initState() {
    super.initState();
    _loadBusinessName();
    _requestRefreshTimer = Timer.periodic(
      const Duration(minutes: 1),
      (_) => _loadBusinessName(),
    );
    _sessionValidationTimer = Timer.periodic(
      const Duration(minutes: 2),
      (_) => _validateCloudSession(),
    );
  }

  Future<void> _validateCloudSession() async {
    if (_forcingCloudLogout || _sessionValidationInFlight || !mounted) return;
    _sessionValidationInFlight = true;
    try {
      await _validateCloudSessionOnce();
    } finally {
      _sessionValidationInFlight = false;
    }
  }

  Future<void> _validateCloudSessionOnce() async {
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return;
    final prefs = await _branchPrefs;
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      await api.validateSession();
    } on CloudApiException catch (error) {
      if (error.statusCode != 401 && error.statusCode != 403) return;
      _forcingCloudLogout = true;
      await storage.delete(key: 'cloud_session_token');
      await prefs.remove('session_user_type');
      await prefs.remove('session_employee_id');
      await prefs.remove('session_employee_name');
      await prefs.remove('session_employee_permissions');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            error.statusCode == 403
                ? 'تم حظر هذا الجهاز'
                : 'تم فصل هذا الجهاز من الحساب',
          ),
        ),
      );
      Navigator.pushAndRemoveUntil(
        context,
        MaterialPageRoute(builder: (_) => const LoginPage()),
        (_) => false,
      );
    } finally {
      api.close();
    }
  }

  Future<void> _loadBusinessName() async {
    if (_dashboardRefreshInFlight) return;
    _dashboardRefreshInFlight = true;
    try {
      await _loadBusinessNameOnce();
    } finally {
      _dashboardRefreshInFlight = false;
    }
  }

  Future<void> _loadBusinessNameOnce() async {
    final prefs = await _branchPrefs;
    if (mounted)
      setState(() => _showHomeBranches = showHomeBranchButton(prefs));
    final savedName =
        prefs.getString('businessName') ??
        prefs.getString('account_business_name') ??
        '';
    final sessionType = prefs.getString('session_user_type') ?? 'admin';
    var subscriptionPackage = prefs.getString('subscription_package') ?? 'free';
    // Only server-approved, unexpired advertisements may be displayed.
    var advertisements = <Map<String, String>>[];
    var welcomeName = savedName;
    Map<String, dynamic> permissions = {};
    if (sessionType == 'employee') {
      welcomeName = prefs.getString('session_employee_name') ?? 'موظف خدووم';
      final employeeId = prefs.getString('session_employee_id');
      final savedEmployees = prefs.getString('business_employees');
      final sessionPermissions = prefs.getString(
        'session_employee_permissions',
      );
      if (sessionPermissions != null) {
        permissions = Map<String, dynamic>.from(
          jsonDecode(sessionPermissions) as Map,
        );
      }
      if (permissions.isEmpty && employeeId != null && savedEmployees != null) {
        final employees = jsonDecode(savedEmployees) as List<dynamic>;
        for (final item in employees) {
          final employee = Map<String, dynamic>.from(item as Map);
          if (employee['id'].toString() == employeeId) {
            permissions = Map<String, dynamic>.from(
              employee['permissions'] as Map? ?? {},
            );
            break;
          }
        }
      }
    }
    var activeReminderCount = 0;
    final savedAppointments = prefs.getString('business_appointments_requests');
    if (savedAppointments != null && savedAppointments.isNotEmpty) {
      try {
        final now = DateTime.now();
        final appointments = jsonDecode(savedAppointments) as List<dynamic>;
        activeReminderCount = appointments.where((rawItem) {
          final item = Map<String, dynamic>.from(rawItem as Map);
          final reminder = DateTime.tryParse(
            item['reminderDate']?.toString() ?? '',
          );
          return item['status'] != 'completed' &&
              reminder != null &&
              reminder.isAfter(now);
        }).length;
      } catch (_) {
        activeReminderCount = 0;
      }
    }
    if (subscriptionPackage != 'vip' && advertisements.isEmpty) {
      advertisements = [
        const <String, String>{
          'title': 'أعلن معنا مجانًا',
          'message': 'عند الترقية إلى باقة VIP',
          'advertiser': 'اضغط لعرض الباقات',
          'promotion': 'vip',
        },
      ];
    }
    if (!mounted) return;
    setState(() {
      _businessName = savedName.trim();
      _welcomeName = welcomeName.trim();
      _businessLogoPath = prefs.getString('business_logo_path');
      _isAdmin = sessionType == 'admin';
      _permissions = permissions;
      _subscriptionPackage = subscriptionPackage;
      _vipAdvertisements = subscriptionPackage == 'vip' ? [] : advertisements;
      _currentAdIndex = 0;
      _activeReminderCount = activeReminderCount;
    });
    _startAdvertisementRotation();
    if (!_alertsSynced) {
      _alertsSynced = true;
      unawaited(
        KhdoomNotifications.syncStoredAlerts().catchError((Object _) {}),
      );
    }
    const cloudStorage = FlutterSecureStorage();
    final cloudToken = await cloudStorage.read(key: 'cloud_session_token');
    var pendingCloudRequestCount = 0;
    var securityAlertCount = 0;
    var assistantLocked = false;
    var assistantLockMessage = 'الخدمة متوقفة مؤقتًا';
    if (cloudToken != null && cloudToken.isNotEmpty) {
      final api = KhdoomCloudApi(
        scope: prefs,
        baseUrl:
            prefs.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      )..token = cloudToken;
      try {
        final organizationFuture = api.organization();
        final advertisementUpdate = () async {
          try {
            final organization = await organizationFuture;
            final cloudPackage = organization['package']?.toString();
            if (cloudPackage == 'free' ||
                cloudPackage == 'basic' ||
                cloudPackage == 'vip') {
              subscriptionPackage = cloudPackage!;
              await prefs.setString('subscription_package', cloudPackage);
            }
            if (subscriptionPackage != 'vip') {
              final ads = await api.advertisements();
              final cloudAdvertisements = ads
                  .map((item) {
                    final ad = Map<String, dynamic>.from(item as Map);
                    return <String, String>{
                      'title': ad['title']?.toString() ?? '',
                      'message': ad['message']?.toString() ?? '',
                      'contact': ad['contact']?.toString() ?? '',
                      'advertiser': ad['advertiser']?.toString() ?? '',
                      'promoCode': ad['promo_code']?.toString() ?? '',
                      'imageData': ad['image_data']?.toString() ?? '',
                      'promotion': ad['ad_source']?.toString() == 'platform'
                          ? 'packages'
                          : '',
                    };
                  })
                  .where((ad) => ad['title']!.isNotEmpty)
                  .toList();
              advertisements = cloudAdvertisements;
            }
            if (subscriptionPackage != 'vip' && advertisements.isEmpty) {
              advertisements = [
                const <String, String>{
                  'title': 'أعلن معنا مجانًا',
                  'message': 'عند الترقية إلى باقة VIP',
                  'advertiser': 'اضغط لعرض الباقات',
                  'promotion': 'vip',
                },
              ];
            }
            if (!mounted) return;
            setState(() {
              _subscriptionPackage = subscriptionPackage;
              _vipAdvertisements = subscriptionPackage == 'vip'
                  ? []
                  : advertisements;
              _currentAdIndex = 0;
            });
            _startAdvertisementRotation();
          } catch (_) {
            // Keep the local promotion when the ad service is unavailable.
          }
        }();
        final initialRequests = await Future.wait<dynamic>([
          organizationFuture,
          api.maintenanceStatus(),
          if (sessionType == 'admin') api.auditLogs(),
          api.appointments(),
          advertisementUpdate,
        ]);
        final maintenance = Map<String, dynamic>.from(
          initialRequests[1] as Map,
        );
        final assistantMaintenance = Map<String, dynamic>.from(
          maintenance['assistant'] as Map? ?? {},
        );
        assistantLocked = assistantMaintenance['active'] == true;
        assistantLockMessage =
            assistantMaintenance['message']?.toString() ??
            'الخدمة متوقفة مؤقتًا';
        if (sessionType == 'admin') {
          final securityActions = {
            'failed_login',
            'suspicious_login',
            'new_device',
            'employee_updated',
            'employee_deleted',
            'device_trust_updated',
            'device_disconnected',
            'device_blocked',
            'device_unblocked',
            'blocked_device_login',
            'support_ticket_updated',
            'sessions_disconnected',
          };
          final logs = List<dynamic>.from(initialRequests[2] as List);
          final lastSeen = prefs.getInt('security_alert_last_seen_id') ?? 0;
          final lastNotified =
              prefs.getInt('security_notification_last_id') ?? 0;
          var newestNotified = lastNotified;
          const notificationActions = {
            'new_device',
            'suspicious_login',
            'employee_updated',
            'device_blocked',
            'blocked_device_login',
            'support_ticket_updated',
          };
          for (final raw in logs.reversed) {
            final item = Map<String, dynamic>.from(raw as Map);
            final id = (item['id'] as num?)?.toInt() ?? 0;
            if (notificationActions.contains(item['action']) &&
                id > lastNotified) {
              await KhdoomNotifications.showSecurityAlert(item);
              if (id > newestNotified) newestNotified = id;
            }
          }
          if (newestNotified > lastNotified) {
            await prefs.setInt('security_notification_last_id', newestNotified);
          }
          securityAlertCount = logs
              .map((item) => Map<String, dynamic>.from(item as Map))
              .where(
                (item) =>
                    securityActions.contains(item['action']) &&
                    ((item['id'] as num?)?.toInt() ?? 0) > lastSeen,
              )
              .length;
        }
        final cloudAppointments = List<dynamic>.from(
          initialRequests[sessionType == 'admin' ? 3 : 2] as List,
        );
        final pendingCloudAppointments = cloudAppointments
            .map((raw) => Map<String, dynamic>.from(raw as Map))
            .where(
              (item) =>
                  item['status'] == 'pending' ||
                  (item['followup_count'] as num? ?? 0) > 0,
            )
            .toList();
        pendingCloudRequestCount = pendingCloudAppointments.length;
        final seenRequestIds =
            prefs.getStringList('seen_cloud_appointment_ids') ?? <String>[];
        final seenSet = seenRequestIds.toSet();
        for (final item in pendingCloudAppointments) {
          final id = (item['followup_count'] as num? ?? 0) > 0
              ? 'followup_${item['id']}_${item['followup_latest_id']}'
              : item['id']?.toString();
          if (id == null || seenSet.contains(id)) continue;
          await KhdoomNotifications.showNewAppointmentRequest(item);
          seenSet.add(id);
        }
        await prefs.setStringList(
          'seen_cloud_appointment_ids',
          seenSet.toList(),
        );
      } catch (_) {
        // تبقى البيانات المحلية متاحة عند تعذر اتصال الخادم.
      } finally {
        api.close();
      }
    }
    if (subscriptionPackage != 'vip' && advertisements.isEmpty) {
      advertisements = [
        const <String, String>{
          'title': 'أعلن معنا مجانًا',
          'message': 'عند الترقية إلى باقة VIP',
          'advertiser': 'اضغط لعرض الباقات',
          'promotion': 'vip',
        },
      ];
    }
    if (!mounted) return;
    setState(() {
      _businessName = savedName.trim();
      _welcomeName = welcomeName.trim();
      _businessLogoPath = prefs.getString('business_logo_path');
      _isAdmin = sessionType == 'admin';
      _permissions = permissions;
      _subscriptionPackage = subscriptionPackage;
      _vipAdvertisements = subscriptionPackage == 'vip' ? [] : advertisements;
      _currentAdIndex = 0;
      _activeReminderCount = activeReminderCount + pendingCloudRequestCount;
      _securityAlertCount = securityAlertCount;
      _assistantLocked = assistantLocked;
      _assistantLockMessage = assistantLockMessage;
    });
    _startAdvertisementRotation();
  }

  void _startAdvertisementRotation() {
    _adRotationTimer?.cancel();
    if (_vipAdvertisements.length < 2) return;
    _adRotationTimer = Timer.periodic(const Duration(seconds: 8), (_) {
      if (!mounted || _vipAdvertisements.isEmpty) return;
      setState(() {
        _currentAdIndex = (_currentAdIndex + 1) % _vipAdvertisements.length;
      });
    });
  }

  Future<void> _showAdvertisementDetails(Map<String, String> ad) async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(ad['title'] ?? 'إعلان'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'المؤسسة: ${ad['advertiser']?.isNotEmpty == true ? ad['advertiser'] : 'غير محددة'}',
            ),
            const SizedBox(height: 10),
            if (ad['message']?.isNotEmpty == true) Text(ad['message']!),
            if (ad['contact']?.isNotEmpty == true) ...[
              const SizedBox(height: 10),
              SelectableText('رقم التواصل: ${ad['contact']}'),
            ],
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('إغلاق'),
          ),
          if (ad['contact']?.isNotEmpty == true)
            FilledButton.icon(
              onPressed: () => launchUrl(Uri.parse('tel:${ad['contact']}')),
              icon: const Icon(Icons.call),
              label: const Text('اتصال'),
            ),
        ],
      ),
    );
  }

  Widget _buildAdvertisementBanner() {
    final ad = _vipAdvertisements[_currentAdIndex % _vipAdvertisements.length];
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Material(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () {
            if (ad['promotion']?.isNotEmpty == true) {
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => SubscriptionPackagesPage(
                    promotionCode: ad['promoCode'] ?? '',
                  ),
                ),
              );
              return;
            }
            _showAdvertisementDetails(ad);
          },
          child: Container(
            width: double.infinity,
            constraints: const BoxConstraints(minHeight: 145),
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: const Color(0xFFF59E0B), width: 1.5),
            ),
            child: AnimatedSwitcher(
              duration: const Duration(milliseconds: 1200),
              transitionBuilder: (child, animation) => FadeTransition(
                opacity: animation,
                child: SlideTransition(
                  position: Tween<Offset>(
                    begin: const Offset(0.15, 0),
                    end: Offset.zero,
                  ).animate(animation),
                  child: child,
                ),
              ),
              child: Row(
                key: ValueKey('${ad['title']}-$_currentAdIndex'),
                children: [
                  if (ad['imageData']?.isNotEmpty == true) ...[
                    ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Image.memory(
                        base64Decode(ad['imageData']!.split(',').last),
                        width: 72,
                        height: 72,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => const Icon(
                          Icons.campaign,
                          color: Color(0xFFF59E0B),
                          size: 46,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                  ] else ...[
                    const Icon(
                      Icons.campaign,
                      color: Color(0xFFF59E0B),
                      size: 46,
                    ),
                    const SizedBox(width: 10),
                  ],
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          ad['title'] ?? 'إعلان',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 19,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 3),
                        Text(
                          ad['message']?.isNotEmpty == true
                              ? ad['message']!
                              : (ad['advertiser'] ?? ''),
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 15,
                            height: 1.4,
                          ),
                        ),
                        const SizedBox(height: 7),
                        Text(
                          ad['promotion']?.isNotEmpty == true
                              ? 'عرض الباقات والاستفادة من الكود'
                              : 'عرض تفاصيل الإعلان',
                          style: const TextStyle(
                            color: Color(0xFFFBBF24),
                            fontSize: 13,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 6),
                  const Icon(
                    Icons.arrow_forward_ios_rounded,
                    color: Color(0xFFFBBF24),
                    size: 18,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    _assistantController.dispose();
    _adRotationTimer?.cancel();
    _requestRefreshTimer?.cancel();
    _sessionValidationTimer?.cancel();
    super.dispose();
  }

  bool _can(String permission) {
    return _isAdmin || _permissions[permission] == true;
  }

  List<Map<String, dynamic>> _assistantList(
    BranchPreferences prefs,
    String key,
  ) {
    final saved = prefs.getString(key);
    if (saved == null || saved.isEmpty) return [];
    try {
      return (jsonDecode(saved) as List)
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
    } catch (_) {
      return [];
    }
  }

  String _assistantDateStatus(dynamic rawDate) {
    final date = DateTime.tryParse(rawDate?.toString() ?? '');
    if (date == null) return 'التاريخ غير مضاف';
    final today = DateTime.now();
    final startToday = DateTime(today.year, today.month, today.day);
    final days = date.difference(startToday).inDays;
    final formatted =
        '${date.day.toString().padLeft(2, '0')}/${date.month.toString().padLeft(2, '0')}/${date.year}';
    if (days < 0) {
      return '$formatted — منتهي منذ ${days.abs()} يوم';
    }
    if (days == 0) return '$formatted — ينتهي اليوم';
    return '$formatted — متبقي $days يوم';
  }

  List<String> _assistantUpcomingItems(
    BranchPreferences prefs,
    List<Map<String, dynamic>> vehicles,
    List<Map<String, dynamic>> employeeAlerts,
  ) {
    final items = <String>[];
    final now = DateTime.now();
    void addIfClose(String title, dynamic rawDate) {
      final date = DateTime.tryParse(rawDate?.toString() ?? '');
      if (date == null) return;
      final days = date
          .difference(DateTime(now.year, now.month, now.day))
          .inDays;
      if (days <= 30) {
        items.add('â€¢ $title: ${_assistantDateStatus(rawDate)}');
      }
    }

    for (final vehicle in vehicles) {
      final name = vehicle['name']?.toString() ?? 'مركبة';
      addIfClose('$name — التأمين', vehicle['insurance']);
      addIfClose('$name — الفحص', vehicle['inspection']);
      addIfClose('$name — الاستمارة', vehicle['registration']);
    }
    for (final employee in employeeAlerts) {
      final name = employee['name']?.toString() ?? 'موظف';
      addIfClose('$name — الإقامة', employee['iqama']);
      addIfClose('$name — العقد', employee['contract']);
      addIfClose('$name — التأمين', employee['insurance']);
    }
    for (final title in const [
      'الرخصة البلدية',
      'السجل التجاري',
      'اشتراك قوى',
      'حماية الأجور – مدد',
      'شهادة الدفاع المدني',
    ]) {
      addIfClose(title, prefs.getString('alert_$title'));
    }
    for (final record in _assistantList(prefs, 'organization_alert_records')) {
      addIfClose(
        record['title']?.toString() ?? 'مستند المؤسسة',
        record['date'],
      );
    }
    return items;
  }

  String _normalizeAssistantText(String value) => value
      .toLowerCase()
      .replaceAll(RegExp(r'[أإآ]'), 'ا')
      .replaceAll('ة', 'ه')
      .replaceAll('ظ‰', 'ظٹ')
      .replaceAll(RegExp(r'[\u064B-\u065F\u0670]'), '')
      .replaceAll(RegExp(r'[^\u0600-\u06FFa-z0-9 ]'), ' ')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();

  final _conversation = AssistantConversation();

  Future<void> _askOrganizationAssistant(String question) async {
    if (_conversation.busy) return;
    if (_assistantLocked) {
      if (mounted)
        setState(() {
          _assistantAnswer = _assistantLockMessage;
          _assistantRecords = [];
          _assistantAction = null;
        });
      return;
    }
    final text = _normalizeAssistantText(question);
    if (text.isEmpty) return;
    final prefs = await _branchPrefs;
    _conversation.bind(prefs.branchId);
    final vehicles = _assistantList(prefs, 'business_vehicles');
    final employees = _assistantList(prefs, 'business_employees');
    final employeeAlerts = _assistantList(prefs, 'employee_alert_records');
    final organizationAlerts = _assistantList(
      prefs,
      'organization_alert_records',
    );
    final bills = _assistantList(prefs, 'electricity_bills');
    final appointments = _assistantList(
      prefs,
      'business_appointments_requests',
    );
    final today = DateTime.now();
    final todayItems = appointments.where((item) {
      final date = DateTime.tryParse(item['date']?.toString() ?? '');
      return date != null &&
          date.year == today.year &&
          date.month == today.month &&
          date.day == today.day &&
          item['status'] != 'completed';
    }).toList();
    final pendingRequests = appointments
        .where(
          (item) => item['type'] == 'طلب عميل' && item['status'] != 'completed',
        )
        .toList();
    final upcoming = _assistantUpcomingItems(prefs, vehicles, employeeAlerts);
    final unpaidBills = bills.where((bill) => bill['paid'] != true).toList();
    String answer;
    VoidCallback? action;
    final attachedRecords = <Map<String, dynamic>>[];

    if (text.contains('وش عندي') ||
        text.contains('ماذا لدي') ||
        text.contains('اليوم') ||
        text.contains('ملخص') ||
        text.contains('وضعي')) {
      final lines = <String>[
        'ملخص المؤسسة:',
        '• الباقة: $_subscriptionPackage',
        if (_can('vehicles')) '• المركبات المسجلة: ${vehicles.length}',
        if (_can('employees')) '• الموظفون المسجلون: ${employees.length}',
        if (_can('appointments'))
          '• التجديدات القريبة أو المنتهية: ${upcoming.length}',
        if (_can('appointments')) '• مواعيد وطلبات اليوم: ${todayItems.length}',
        if (_can('appointments'))
          '• الطلبات المعلقة: ${pendingRequests.length}',
        if (_can('appointments'))
          '• فواتير الكهرباء غير المسددة: ${unpaidBills.length}',
      ];
      if (_can('appointments') && upcoming.isNotEmpty) {
        lines.add('');
        lines.add('الأهم الآن:');
        lines.addAll(upcoming.take(5));
      }
      answer = lines.join('\n');
    } else if (_can('appointments') &&
        organizationAlerts.any((record) {
          final title = _normalizeAssistantText(
            record['title']?.toString() ?? '',
          );
          if (title.isEmpty) return false;
          if (text.contains(title) || title.contains(text)) return true;
          return title
              .split(RegExp(r'\s+|â€“|-'))
              .where((word) => word.length >= 3)
              .any(text.contains);
        })) {
      final record = organizationAlerts.firstWhere((record) {
        final title = _normalizeAssistantText(
          record['title']?.toString() ?? '',
        );
        if (text.contains(title) || title.contains(text)) return true;
        return title
            .split(RegExp(r'\s+|â€“|-'))
            .where((word) => word.length >= 3)
            .any(text.contains);
      });
      final title = record['title']?.toString() ?? 'مستند المؤسسة';
      final details = record['details']?.toString().trim() ?? '';
      final website = record['website']?.toString().trim() ?? '';
      final date = record['date']?.toString() ?? '';
      attachedRecords.add(record);
      answer = [
        'معلومات $title:',
        if (details.isNotEmpty) details,
        'الحالة: ${_assistantDateStatus(date)}',
        if (website.isNotEmpty)
          'يمكنك فتح الموقع الرسمي من الزر أدناه لمشاهدة بقية المستندات.',
      ].join('\n');
      action = website.isNotEmpty
          ? () => openDocumentWebsite(context, website)
          : () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const OrganizationAlertsPage()),
            );
    } else if (text.contains('موعد') ||
        text.contains('مواعيد') ||
        text.contains('طلب') ||
        text.contains('عميل')) {
      if (!_can('appointments')) {
        answer = 'ليس لديك صلاحية لعرض المواعيد والطلبات.';
      } else {
        final wantsAll = text.contains('جميع') || text.contains('الكل');
        String? requestedType;
        if (!wantsAll && text.contains('مقاس')) {
          requestedType = 'موعد مقاس';
        } else if (!wantsAll && text.contains('صيان')) {
          requestedType = 'موعد صيانة';
        } else if (!wantsAll &&
            (text.contains('طلب عميل') ||
                text.contains('طلبات العملاء') ||
                text.contains('طلبات عميل'))) {
          requestedType = 'طلب عميل';
        }
        final pending =
            appointments
                .where(
                  (item) =>
                      item['status'] != 'completed' &&
                      (requestedType == null || item['type'] == requestedType),
                )
                .toList()
              ..sort((a, b) {
                final first = DateTime.tryParse(a['date']?.toString() ?? '');
                final second = DateTime.tryParse(b['date']?.toString() ?? '');
                if (first == null || second == null) return 0;
                return first.compareTo(second);
              });
        if (text.contains('كم') || text.contains('عدد')) {
          final label = requestedType ?? 'المواعيد والطلبات';
          answer = 'عدد $label المعلقة: ${pending.length}.';
        } else if (pending.isEmpty) {
          answer = requestedType == null
              ? 'لا توجد مواعيد أو طلبات معلقة.'
              : 'لا يوجد ' + requestedType + ' معلق حاليًا.';
        } else {
          final heading = requestedType == null
              ? 'جميع المواعيد والطلبات القادمة (' +
                    pending.length.toString() +
                    ')'
              : requestedType + ' القادمة (' + pending.length.toString() + ')';
          final details = pending
              .take(8)
              .map((item) {
                final type = item['type']?.toString() ?? 'موعد';
                final title = item['title']?.toString() ?? 'بدون عنوان';
                final customer = item['customer']?.toString() ?? '';
                final customerText = customer.isEmpty
                    ? ''
                    : ' — العميل: ' + customer;
                return 'â€¢ ' +
                    type +
                    ': ' +
                    title +
                    customerText +
                    '\n  ' +
                    _assistantDateStatus(item['date']);
              })
              .join('\n');
          answer = heading + ':\n' + details;
        }
        action = () async {
          await Navigator.push<void>(
            context,
            MaterialPageRoute(builder: (_) => const AppointmentsRequestsPage()),
          );
          await _loadBusinessName();
        };
      }
    } else if (text.contains('منتهي') ||
        text.contains('ينتهي') ||
        text.contains('تجديد') ||
        text.contains('تنبيه') ||
        text.contains('قريب')) {
      if (!_can('appointments')) {
        answer = 'ليس لديك صلاحية لعرض التنبيهات والتجديدات.';
      } else if (upcoming.isEmpty) {
        answer = 'لا توجد تواريخ منتهية أو تنتهي خلال 30 يومًا حسب البيانات المسجلة.';
      } else {
        answer =
            'التواريخ المنتهية أو القريبة خلال 30 يومًا:\n${upcoming.take(10).join('\n')}';
        action = () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const AlertsPage()),
        );
      }
    } else if (text.contains('كهرب') || text.contains('فاتور')) {
      if (!_can('appointments')) {
        answer = 'ليس لديك صلاحية لعرض الفواتير والتنبيهات.';
      } else if (bills.isEmpty) {
        answer = 'لا توجد فواتير كهرباء مسجلة.';
      } else if (unpaidBills.isEmpty) {
        answer = 'جميع فواتير الكهرباء المسجلة مسددة.';
      } else {
        final details = unpaidBills
            .take(8)
            .map(
              (bill) =>
                  '• ${bill['month']?.toString() ?? 'شهر غير محدد'}: ${bill['amount']?.toString() ?? '0'} ريال — غير مسددة',
            )
            .join('\n');
        answer =
            'فواتير الكهرباء غير المسددة (${unpaidBills.length}):\n$details';
        action = () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const OrganizationAlertsPage()),
        );
      }
    } else if (text.contains('مركب') ||
        text.contains('سيار') ||
        text.contains('لوح') ||
        text.contains('تأمين') ||
        text.contains('فحص') ||
        text.contains('استمار')) {
      if (!_can('vehicles')) {
        answer = 'ليس لديك صلاحية لعرض بيانات المركبات.';
      } else if (vehicles.isEmpty) {
        answer = 'لا توجد مركبات مسجلة حتى الآن.';
      } else if (text.contains('كم') || text.contains('عدد')) {
        answer = vehicles.length == 1
            ? 'لديك مركبة واحدة مسجلة.'
            : 'لديك ${vehicles.length} مركبات مسجلة.';
        action = () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const VehiclesPage()),
        );
      } else {
        final matching = vehicles.where((vehicle) {
          final name = _normalizeAssistantText(
            vehicle['name']?.toString() ?? '',
          );
          final plate = _normalizeAssistantText(
            vehicle['plate']?.toString() ?? '',
          );
          final assigned = _normalizeAssistantText(
            vehicle['assignedEmployee']?.toString() ?? '',
          );
          final status = vehicle['status']?.toString() ?? 'working';
          final statusMatches =
              (text.contains('صيان') && status == 'maintenance') ||
              (text.contains('عطل') && status == 'broken') ||
              (text.contains('متوقف') && status == 'stopped');
          return (name.isNotEmpty && text.contains(name)) ||
              (plate.isNotEmpty && text.contains(plate)) ||
              (assigned.isNotEmpty && text.contains(assigned)) ||
              statusMatches;
        }).toList();
        final selected = matching.isEmpty ? vehicles.take(5) : matching;
        attachedRecords.addAll(selected);
        answer = selected
            .map(
              (vehicle) =>
                  '🚗 ${vehicle['name']?.toString() ?? 'مركبة'}${(vehicle['plate']?.toString() ?? '').isEmpty ? '' : '\nاللوحة: ${vehicle['plate']}'}\nالموظف المسؤول: ${(vehicle['assignedEmployee']?.toString() ?? '').isEmpty ? 'غير محدد' : vehicle['assignedEmployee']}\nالحالة: ${const {'working': 'تعمل', 'stopped': 'متوقفة', 'broken': 'عطلانة', 'maintenance': 'تحت الصيانة'}[vehicle['status']] ?? 'تعمل'}\nالتأمين: ${_assistantDateStatus(vehicle['insurance'])}\nالفحص: ${_assistantDateStatus(vehicle['inspection'])}\nالاستمارة: ${_assistantDateStatus(vehicle['registration'])}',
            )
            .join('\n\n');
        action = () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const VehiclesPage()),
        );
      }
    } else if (text.contains('موظف') ||
        text.contains('عامل') ||
        text.contains('إقامة') ||
        text.contains('اقامة') ||
        text.contains('عقد')) {
      if (!_can('employees')) {
        answer = 'ليس لديك صلاحية لعرض بيانات الموظفين.';
      } else if (employees.isEmpty) {
        answer = 'لا يوجد موظفون مسجلون حتى الآن.';
      } else {
        final matching = employees.where((employee) {
          final name = _normalizeAssistantText(
            employee['name']?.toString() ?? '',
          );
          return name.isNotEmpty && text.contains(name);
        }).toList();
        final selected = matching.isEmpty ? employees.take(8) : matching;
        attachedRecords.addAll(selected);
        for (final employee in selected) {
          attachedRecords.addAll(
            employeeAlerts.where(
              (record) =>
                  _normalizeAssistantText(record['name']?.toString() ?? '') ==
                  _normalizeAssistantText(employee['name']?.toString() ?? ''),
            ),
          );
        }
        answer = selected
            .map(
              (employee) =>
                  '👤 ${employee['name']?.toString() ?? 'موظف'}\nالوظيفة: ${employee['role']?.toString() ?? 'غير محددة'}\nالحالة: ${employee['active'] == false ? 'موقوف' : 'نشط'}',
            )
            .join('\n\n');
      }
    } else if (text.contains('فرع') || text.contains('فروع')) {
      answer =
          'أنت الآن في ${prefs.branchName}. تبي تعرض بيانات هذا الفرع أو تبدّل لفرع آخر؟ التبديل من الإعدادات ← إدارة الفروع.';
    } else if (text.contains('باقه') ||
        text.contains('اشتراك') ||
        text.contains('خطه')) {
      answer = 'الباقة الحالية: $_subscriptionPackage.';
    } else if (text.contains('مؤسس') ||
        text.contains('نشاط') ||
        text.contains('تواصل') ||
        text.contains('رقم')) {
      if (!_can('settings')) {
        answer = 'ليس لديك صلاحية لعرض بيانات المؤسسة.';
      } else {
        final name = _businessName.isEmpty ? 'غير محددة' : _businessName;
        final activity = prefs.getString('activity') ?? 'غير محدد';
        final phone =
            prefs.getString('phone') ??
            prefs.getString('account_phone') ??
            'غير مضاف';
        answer =
            'بيانات المؤسسة:\n• الاسم: $name\n• النشاط: $activity\n• رقم التواصل: $phone';
        attachedRecords.add({
          'title': name,
          'imagePath': prefs.getString('business_logo_path') ?? '',
        });
        action = () => Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const MyBusinessPage()),
        );
      }
    } else {
      answer = await _conversation.ask(prefs, question);
    }

    if (!mounted) return;
    setState(() {
      _assistantAnswer = answer;
      _assistantAction = action;
      _assistantRecords = attachedRecords;
    });
    _conversation.remember(question, answer);
  }

  Widget _dailyWorkPage() => DailyWorkPage(destinations: dailyWorkDestinations);

  Widget _buildAssistantSectionsGrid() {
    return GridView.count(
      crossAxisCount: 2,
      crossAxisSpacing: 14,
      mainAxisSpacing: 14,
      // بطاقات التصنيفات تحتوي عنوانًا ووصفًا بالعربية. تقليل النسبة يزيد
      // الارتفاع المتاح لها على شاشات الجوال القصيرة ويمنع تجاوز المحتوى.
      // ارتفاع أكبر للبطاقات يمنع قص النص العربي على الشاشات القصيرة.
      // Extra height prevents Arabic text from overflowing on short phones and with larger system text.
      childAspectRatio: 0.62,
      children: [
        if (_isAdmin)
          DashboardCard(
            icon: Icons.today_outlined,
            title: 'لوحتي اليومية',
            subtitle: 'المستحقات ومهام المتابعة',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => _dailyWorkPage()),
            ),
          ),
        if (_isAdmin)
          DashboardCard(
            icon: Icons.badge_outlined,
            title: 'إدارة الموظفين',
            subtitle: 'الملفات والرواتب والإجازات',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => EmployeeManagementPage(
                  onAlertsChanged: KhdoomNotifications.syncStoredAlerts,
                ),
              ),
            ),
          ),
        if (_can('settings'))
          DashboardCard(
            icon: Icons.business,
            title: _businessName.isEmpty ? 'بيانات المؤسسة' : _businessName,
            subtitle: 'بيانات المؤسسة',
            onTap: () async {
              await Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const MyBusinessPage()),
              );
              await _loadBusinessName();
            },
          ),
        DashboardCard(
          icon: Icons.forum_outlined,
          title: 'مجتمع خدوم',
          subtitle: 'تواصل مع المؤسسات داخل التطبيق',
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const CommunityPage()),
          ),
        ),
        if (_can('appointments'))
          DashboardCard(
            icon: Icons.notifications_active,
            title: 'التنبيهات',
            subtitle: 'التجديدات والفواتير',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const AlertsPage()),
            ),
          ),
        if (_can('appointments'))
          DashboardCard(
            icon: Icons.event_note,
            title: 'المواعيد والطلبات',
            subtitle: 'مواعيد العملاء والطلبات الجديدة',
            onTap: () async {
              await Navigator.push<void>(
                context,
                MaterialPageRoute(
                  builder: (_) => const AppointmentsRequestsPage(),
                ),
              );
              await _loadBusinessName();
            },
          ),
        if (_can('conversations') || _can('employees'))
          DashboardCard(
            icon: Icons.smart_toy,
            title: 'موظفو AI',
            subtitle: 'إدارة الموظفين الذكيين',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) =>
                    AiEmployeesPage(subscriptionPackage: _subscriptionPackage),
              ),
            ),
          ),
        if (_can('vehicles'))
          DashboardCard(
            icon: Icons.directions_car,
            title: 'المركبات',
            subtitle: 'متابعة المركبات',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const VehiclesPage()),
            ),
          ),
        DashboardCard(
          icon: Icons.school,
          title: 'التعليم',
          subtitle: 'تعلم استخدام خدووم',
          onTap: () => Navigator.push(
            context,
            MaterialPageRoute(builder: (_) => const EducationPage()),
          ),
        ),
        DashboardCard(
          icon: Icons.settings,
          title: 'الإعدادات',
          subtitle: 'إعدادات الحساب',
          onTap: () async {
            await Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsPage()),
            );
            if (mounted) await _loadBusinessName();
          },
        ),
      ],
    );
  }

  Future<void> _openAssistantCategories() async {
    await Navigator.push<void>(
      context,
      MaterialPageRoute(
        builder: (_) => Directionality(
          textDirection: TextDirection.rtl,
          child: Scaffold(
            backgroundColor: const Color(0xFF0B1020),
            appBar: AppBar(
              actions: [PageRefreshButton(onRefresh: _loadBusinessName)],
              backgroundColor: const Color(0xFF111B35),
              foregroundColor: Colors.white,
              centerTitle: true,
              title: const Text(
                'التصنيفات',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            body: SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Align(
                  alignment: Alignment.topCenter,
                  child: _buildAssistantSectionsGrid(),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildOrganizationAssistant() {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF172554), Color(0xFF312E81)],
        ),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xFF38BDF8)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _assistantController,
            textInputAction: TextInputAction.send,
            onSubmitted: (value) {
              _askOrganizationAssistant(value);
              _assistantController.clear();
            },
            style: const TextStyle(color: Colors.white),
            decoration: InputDecoration(
              hintText: 'اسألني',
              hintStyle: const TextStyle(color: Colors.white54),
              filled: true,
              fillColor: const Color(0xFF0B1020),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(14),
                borderSide: BorderSide.none,
              ),
              suffixIcon: IconButton(
                onPressed: () {
                  _askOrganizationAssistant(_assistantController.text);
                  _assistantController.clear();
                },
                icon: const Icon(Icons.send, color: Color(0xFF67E8F9)),
              ),
            ),
          ),
          const SizedBox(height: 10),
          Container(
            width: double.infinity,
            constraints: BoxConstraints(
              minHeight:
                  MediaQuery.sizeOf(context).height < 850 &&
                      _assistantAnswer.isEmpty
                  ? 90
                  : 115,
            ),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: const Color(0xFF0B1020),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: Colors.white12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _assistantAnswer.isEmpty
                      ? 'تظهر إجابة مساعد المؤسسة هنا'
                      : _assistantAnswer,
                  style: TextStyle(
                    color: _assistantAnswer.isEmpty
                        ? Colors.white38
                        : Colors.white,
                    height: 1.5,
                  ),
                ),
                RecordAttachments(records: _assistantRecords),
                if (_assistantAction != null) ...[
                  const SizedBox(height: 8),
                  FilledButton.icon(
                    onPressed: _assistantAction,
                    icon: const Icon(Icons.open_in_new),
                    label: const Text('فتح التفاصيل أو الموقع'),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 10),
          CategoriesBranchActions(
            compactHeight: MediaQuery.sizeOf(context).height < 850,
            showBranches: _showHomeBranches,
            onCategories: _openAssistantCategories,
            onBranchChanged: () => Navigator.pushAndRemoveUntil(
              context,
              MaterialPageRoute(builder: (_) => const DashboardPage()),
              (_) => false,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        resizeToAvoidBottomInset: false,
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          elevation: 0,
          title: const Text(
            'خدووم',
            style: TextStyle(fontWeight: FontWeight.bold, fontSize: 22),
          ),
          centerTitle: true,
          actions: [
            if (_isAdmin)
              IconButton(
                tooltip: 'لوحتي اليومية',
                icon: const Icon(Icons.today_outlined),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => _dailyWorkPage()),
                ),
              ),
            if (_isAdmin) PageRefreshButton(onRefresh: _loadBusinessName),
            if (_isAdmin)
              IconButton(
                tooltip: 'التنبيهات الأمنية',
                onPressed: () async {
                  await Navigator.push<void>(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const SecurityAlertsPage(),
                    ),
                  );
                  await _loadBusinessName();
                },
                icon: Badge.count(
                  count: _securityAlertCount,
                  isLabelVisible: _securityAlertCount > 0,
                  backgroundColor: const Color(0xFFEF4444),
                  child: const Icon(
                    Icons.security_outlined,
                    color: Color(0xFF7DD3FC),
                  ),
                ),
              ),
            IconButton(
              tooltip: 'المواعيد والتنبيهات',
              onPressed: () async {
                await Navigator.push<void>(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const AppointmentsRequestsPage(),
                  ),
                );
                await _loadBusinessName();
              },
              icon: Badge.count(
                count: _activeReminderCount,
                isLabelVisible: _activeReminderCount > 0,
                backgroundColor: const Color(0xFFEF4444),
                child: const Icon(
                  Icons.notifications_active_outlined,
                  color: Color(0xFFFBBF24),
                ),
              ),
            ),
          ],
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
            child: ListView(
              children: [
                Row(
                  children: [
                    if (_businessLogoPath != null &&
                        File(_businessLogoPath!).existsSync()) ...[
                      ClipOval(
                        child: DocumentImage(
                          path: _businessLogoPath!,
                          title: 'شعار المؤسسة',
                          width: 68,
                          height: 68,
                        ),
                      ),
                      const SizedBox(width: 12),
                    ],
                    Expanded(
                      child: Text(
                        _welcomeName.isEmpty
                            ? 'مرحبًا بك 👋'
                            : 'مرحبًا، $_welcomeName 👋',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 26,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                const Text(
                  'كل أعمال مؤسستك في مكان واحد',
                  style: TextStyle(color: Colors.white70, fontSize: 15),
                ),
                const SizedBox(height: 16),
                _buildOrganizationAssistant(),
                const SizedBox(height: 16),

                if (_subscriptionPackage != 'vip' &&
                    _vipAdvertisements.isNotEmpty)
                  _buildAdvertisementBanner(),
                if (_subscriptionPackage == 'vip' && _isAdmin)
                  const VipAdvertisementCard(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class EducationPage extends StatelessWidget {
  final bool isOnboarding;

  const EducationPage({super.key, this.isOnboarding = false});

  static const _lessons = [
    (
      Icons.home_work_outlined,
      'إعداد مؤسستي',
      'أضف اسم المؤسسة ونشاطها ورقم التواصل من صفحة مؤسستي.',
    ),
    (
      Icons.smart_toy_outlined,
      'إعداد موظف AI',
      'اختر موظف الذكاء الاصطناعي وحدد الخدمات وطريقة الرد على العملاء.',
    ),
    (
      Icons.directions_car_outlined,
      'إدارة المركبات',
      'أضف المركبة وتواريخ انتهاء الاستمارة والفحص والتأمين.',
    ),
    (
      Icons.manage_accounts_outlined,
      'الموظفون والصلاحيات',
      'أنشئ لكل موظف اسم مستخدم وكلمة مرور وحدد الأقسام المسموحة.',
    ),
    (
      Icons.notifications_active_outlined,
      'التنبيهات',
      'راجع المواعيد والتجديدات حتى لا يفوتك تاريخ مهم.',
    ),
    (
      Icons.settings_outlined,
      'الإعدادات والحساب',
      'غيّر صورة العرض واضبط التنبيهات أو سجّل الخروج من الحساب.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          automaticallyImplyLeading: !isOnboarding,
          title: Text(isOnboarding ? 'مرحبًا بك في خدووم' : 'تعليم خدووم'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
        ),
        body: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(18, 20, 18, 12),
                child: Column(
                  children: [
                    const Icon(
                      Icons.school,
                      color: Color(0xFF38BDF8),
                      size: 58,
                    ),
                    const SizedBox(height: 10),
                    Text(
                      isOnboarding
                          ? 'هذا تعليم مبدئي سريع. يمكنك الرجوع إليه لاحقًا من خانة التعليم.'
                          : 'اختر الموضوع الذي تريد معرفة طريقة استخدامه.',
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 16,
                        height: 1.5,
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView.separated(
                  padding: const EdgeInsets.all(16),
                  itemCount: _lessons.length + (isOnboarding ? 0 : 2),
                  separatorBuilder: (_, _) => const SizedBox(height: 10),
                  itemBuilder: (context, index) {
                    if (!isOnboarding && index < 2) {
                      return ListTile(
                        tileColor: const Color(0xFF172554),
                        leading: const Icon(
                          Icons.quiz_outlined,
                          color: Color(0xFF38BDF8),
                        ),
                        title: Text(
                          index == 0
                              ? 'تعليم الموظف: سؤال وجواب'
                              : 'تدريب الموظفين بالمحادثة',
                          style: const TextStyle(color: Colors.white),
                        ),
                        subtitle: Text(
                          index == 0
                              ? 'اكتب السؤال والإجابة واحفظهما للاستقبال أو أي موظف'
                              : 'فتح طريقة التدريب السابقة',
                          style: const TextStyle(color: Colors.white70),
                        ),
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => index == 0
                                ? const QuestionAnswerTrainingPage()
                                : const AiTrainingPage(),
                          ),
                        ),
                      );
                    }
                    final lesson = _lessons[index - (isOnboarding ? 0 : 2)];
                    return ExpansionTile(
                      collapsedBackgroundColor: const Color(0xFF172554),
                      backgroundColor: const Color(0xFF172554),
                      collapsedIconColor: const Color(0xFF7DD3FC),
                      iconColor: const Color(0xFF38BDF8),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(15),
                      ),
                      collapsedShape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(15),
                      ),
                      leading: Icon(lesson.$1, color: const Color(0xFF38BDF8)),
                      title: Text(
                        lesson.$2,
                        style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
                      children: [
                        Text(
                          lesson.$3,
                          style: const TextStyle(
                            color: Colors.white70,
                            height: 1.6,
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
              if (isOnboarding)
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: FilledButton.icon(
                    onPressed: () => Navigator.pushAndRemoveUntil(
                      context,
                      MaterialPageRoute(builder: (_) => const DashboardPage()),
                      (_) => false,
                    ),
                    style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(54),
                      backgroundColor: const Color(0xFF38BDF8),
                      foregroundColor: const Color(0xFF0B1020),
                    ),
                    icon: const Icon(Icons.check_circle_outline),
                    label: const Text(
                      'فهمت، ابدأ استخدام خدووم',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class VehicleFormDialog extends StatefulWidget {
  const VehicleFormDialog({super.key, this.initial});

  final Map<String, dynamic>? initial;

  @override
  State<VehicleFormDialog> createState() => _VehicleFormDialogState();
}

class _VehicleFormDialogState extends State<VehicleFormDialog> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  final _nameController = TextEditingController();
  final _plateController = TextEditingController();
  DateTime? _registration;
  DateTime? _inspection;
  DateTime? _insurance;
  String? _imagePath;
  List<String> _employeeNames = [];
  String _assignedEmployee = '';
  String _vehicleStatus = 'working';

  @override
  void initState() {
    super.initState();
    final initial = widget.initial;
    if (initial != null) {
      _nameController.text = initial['name']?.toString() ?? '';
      _plateController.text = initial['plate']?.toString() ?? '';
      _registration = DateTime.tryParse(
        initial['registration']?.toString() ?? '',
      );
      _inspection = DateTime.tryParse(initial['inspection']?.toString() ?? '');
      _insurance = DateTime.tryParse(initial['insurance']?.toString() ?? '');
      _imagePath = initial['imagePath']?.toString();
      _assignedEmployee = initial['assignedEmployee']?.toString() ?? '';
      _vehicleStatus = initial['status']?.toString() ?? 'working';
    }
    _loadEmployees();
  }

  Future<void> _loadEmployees() async {
    final prefs = await _branchPrefs;
    final names = KhdoomNotifications._storedList(prefs, 'business_employees')
        .map((item) => item['name']?.toString().trim() ?? '')
        .where((name) => name.isNotEmpty)
        .toSet()
        .toList();
    if (_assignedEmployee.isNotEmpty && !names.contains(_assignedEmployee)) {
      names.add(_assignedEmployee);
    }
    if (mounted) setState(() => _employeeNames = names);
  }

  @override
  void dispose() {
    _nameController.dispose();
    _plateController.dispose();
    super.dispose();
  }

  String _format(DateTime? date) => date == null
      ? 'اختر التاريخ'
      : '${date.year}/${date.month.toString().padLeft(2, '0')}/${date.day.toString().padLeft(2, '0')}';

  Future<void> _choose(String type) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: DateTime.now().add(const Duration(days: 30)),
      firstDate: DateTime.now().subtract(const Duration(days: 3650)),
      lastDate: DateTime.now().add(const Duration(days: 7300)),
    );
    if (picked == null || !mounted) return;
    setState(() {
      if (type == 'registration') _registration = picked;
      if (type == 'inspection') _inspection = picked;
      if (type == 'insurance') _insurance = picked;
    });
  }

  Future<void> _pickImage() async {
    try {
      const channel = MethodChannel('khdoom/profile_image');
      final path = await channel.invokeMethod<String>('pickImage', {
        'fileName':
            'khdoom_vehicle_${widget.initial?['id'] ?? DateTime.now().millisecondsSinceEpoch}',
      });
      if (path != null && path.isNotEmpty && mounted) {
        setState(() => _imagePath = path);
      }
    } on PlatformException catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('تعذر اختيار الصورة')));
    }
  }

  InputDecoration _decoration(String label, IconData icon) => InputDecoration(
    labelText: label,
    labelStyle: const TextStyle(color: Colors.white70),
    prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
    filled: true,
    fillColor: const Color(0xFF0B1020),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: BorderSide.none,
    ),
  );

  Widget _dateTile(String title, IconData icon, DateTime? value, String type) =>
      ListTile(
        contentPadding: EdgeInsets.zero,
        leading: Icon(icon, color: const Color(0xFF7DD3FC)),
        title: Text(title, style: const TextStyle(color: Colors.white)),
        subtitle: Text(
          _format(value),
          style: const TextStyle(color: Colors.white60),
        ),
        trailing: const Icon(Icons.calendar_month, color: Color(0xFF38BDF8)),
        onTap: () => _choose(type),
      );

  void _save() {
    if (_nameController.text.trim().isEmpty ||
        _registration == null ||
        _inspection == null ||
        _insurance == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('اكتب اسم المركبة وحدد جميع تواريخ الانتهاء'),
        ),
      );
      return;
    }
    Navigator.pop(context, <String, dynamic>{
      'id':
          widget.initial?['id'] ??
          DateTime.now().microsecondsSinceEpoch.toString(),
      'name': _nameController.text.trim(),
      'plate': _plateController.text.trim(),
      'registration': _registration!.toIso8601String(),
      'inspection': _inspection!.toIso8601String(),
      'insurance': _insurance!.toIso8601String(),
      'imagePath': _imagePath ?? '',
      'assignedEmployee': _assignedEmployee,
      'status': _vehicleStatus,
    });
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: Text(
      widget.initial == null ? 'إضافة مركبة' : 'تعديل المركبة',
      style: const TextStyle(color: Colors.white),
    ),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _nameController,
            style: const TextStyle(color: Colors.white),
            decoration: _decoration('اسم أو نوع المركبة', Icons.directions_car),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            initialValue: _assignedEmployee.isEmpty ? '' : _assignedEmployee,
            decoration: _decoration(
              'الموظف المسؤول عن المركبة',
              Icons.badge_outlined,
            ),
            dropdownColor: const Color(0xFF172554),
            style: const TextStyle(color: Colors.white),
            items: [
              const DropdownMenuItem(value: '', child: Text('بدون موظف محدد')),
              ..._employeeNames.map(
                (name) => DropdownMenuItem(value: name, child: Text(name)),
              ),
            ],
            onChanged: (value) =>
                setState(() => _assignedEmployee = value ?? ''),
          ),
          const SizedBox(height: 10),
          DropdownButtonFormField<String>(
            initialValue: _vehicleStatus,
            decoration: _decoration('حالة المركبة', Icons.car_repair_outlined),
            dropdownColor: const Color(0xFF172554),
            style: const TextStyle(color: Colors.white),
            items: const [
              DropdownMenuItem(value: 'working', child: Text('تعمل')),
              DropdownMenuItem(value: 'stopped', child: Text('متوقفة')),
              DropdownMenuItem(value: 'broken', child: Text('عطلانة')),
              DropdownMenuItem(
                value: 'maintenance',
                child: Text('تحت الصيانة'),
              ),
            ],
            onChanged: (value) =>
                setState(() => _vehicleStatus = value ?? 'working'),
          ),
          const SizedBox(height: 12),
          if (_imagePath != null && _imagePath!.isNotEmpty)
            ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: DocumentImage(
                path: _imagePath!,
                height: 130,
                width: double.infinity,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => const SizedBox.shrink(),
              ),
            ),
          OutlinedButton.icon(
            onPressed: _pickImage,
            icon: const Icon(Icons.add_a_photo_outlined),
            label: Text(
              _imagePath == null || _imagePath!.isEmpty
                  ? 'إضافة صورة'
                  : 'تغيير الصورة',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _plateController,
            style: const TextStyle(color: Colors.white),
            decoration: _decoration('رقم اللوحة', Icons.pin_outlined),
          ),
          const SizedBox(height: 8),
          _dateTile(
            'انتهاء الاستمارة',
            Icons.description_outlined,
            _registration,
            'registration',
          ),
          _dateTile(
            'انتهاء الفحص',
            Icons.fact_check_outlined,
            _inspection,
            'inspection',
          ),
          _dateTile(
            'انتهاء التأمين',
            Icons.verified_user_outlined,
            _insurance,
            'insurance',
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('إلغاء'),
      ),
      FilledButton(onPressed: _save, child: const Text('حفظ')),
    ],
  );
}

class VehiclesPage extends StatefulWidget {
  final String? recordId;
  const VehiclesPage({super.key, this.recordId});

  @override
  State<VehiclesPage> createState() => _VehiclesPageState();
}

class _VehiclesPageState extends State<VehiclesPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  static const _storageKey = 'business_vehicles';
  final List<Map<String, dynamic>> _vehicles = [];
  bool _loading = true;
  bool _canEdit = true;
  bool _canDelete = true;
  List<int> get _visibleIndexes => [
    for (var i = 0; i < _vehicles.length; i++)
      if (widget.recordId == null ||
          _vehicles[i]['id'].toString() == widget.recordId)
        i,
  ];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await _branchPrefs;
    // Refresh in the background; opening the form must not wait for the network.
    unawaited(PackageResourceLimits.load(prefs));
    if (prefs.getString('session_user_type') == 'employee') {
      _canEdit = false;
      _canDelete = false;
      final employeeId = prefs.getString('session_employee_id');
      final employeesJson = prefs.getString('business_employees');
      if (employeeId != null && employeesJson != null) {
        for (final item in jsonDecode(employeesJson) as List) {
          final employee = Map<String, dynamic>.from(item as Map);
          if (employee['id'].toString() == employeeId) {
            final permissions = Map<String, dynamic>.from(
              employee['permissions'] as Map? ?? {},
            );
            _canEdit = permissions['editVehicles'] == true;
            _canDelete = permissions['deleteVehicles'] == true;
            break;
          }
        }
      }
    }
    final saved = prefs.getString(_storageKey);
    _vehicles.clear();
    if (saved != null) {
      _vehicles.addAll(
        (jsonDecode(saved) as List).map(
          (e) => Map<String, dynamic>.from(e as Map),
        ),
      );
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _save() async {
    final prefs = await _branchPrefs;
    await prefs.setString(_storageKey, jsonEncode(_vehicles));
    await KhdoomNotifications.syncStoredAlerts();
  }

  String _format(DateTime? date) => date == null
      ? 'اختر التاريخ'
      : '${date.year}/${date.month.toString().padLeft(2, '0')}/${date.day.toString().padLeft(2, '0')}';

  Future<void> _showForm({int? index}) async {
    final prefs = await _branchPrefs;
    final package = prefs.getString('subscription_package') ?? 'free';
    final limits = index == null
        ? PackageResourceLimits.cached(prefs)
        : PackageResourceLimits.defaults;
    final vehicleLimit = limits.limit(package, 'vehicles');
    if (index == null &&
        vehicleLimit != null &&
        prefs.totalRecords(_storageKey) >= vehicleLimit) {
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('وصلت إلى حد الباقة'),
          content: Text(
            'وصلت إلى حد باقتك الحالي: $vehicleLimit مركبات. يمكنك ترقية الباقة أو التواصل مع الإدارة.',
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('حسنًا'),
            ),
          ],
        ),
      );
      return;
    }
    if (!mounted) return;
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) =>
          VehicleFormDialog(initial: index == null ? null : _vehicles[index]),
    );
    if (result == null || !mounted) return;
    setState(() {
      if (index == null) {
        _vehicles.add(result);
      } else {
        _vehicles[index] = result;
      }
    });
    await _save();
  }

  Future<void> _delete(int index) async {
    final confirmationController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('حذف المركبة'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('سيتم حذف ${_vehicles[index]['name']} نهائيًا.'),
              const SizedBox(height: 12),
              const Text('اكتب كلمة «تأكيد» لإكمال الحذف.'),
              TextField(
                controller: confirmationController,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'تأكيد العملية'),
                onChanged: (_) => setDialogState(() {}),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed:
                  {
                    'تأكيد',
                    'تاكيد',
                  }.contains(confirmationController.text.trim())
                  ? () => Navigator.pop(dialogContext, true)
                  : null,
              child: const Text('حذف'),
            ),
          ],
        ),
      ),
    );
    await Future<void>.delayed(const Duration(milliseconds: 400));
    confirmationController.dispose();
    if (confirmed != true || !mounted) return;
    setState(() => _vehicles.removeAt(index));
    await _save();
  }

  Widget _dateRow(String title, String value) => Padding(
    padding: const EdgeInsets.only(top: 7),
    child: Row(
      children: [
        Expanded(
          child: Text(title, style: const TextStyle(color: Colors.white60)),
        ),
        Text(
          DateTime.tryParse(value) == null
              ? 'غير مضاف'
              : _format(DateTime.tryParse(value)),
          style: const TextStyle(
            color: Color(0xFF7DD3FC),
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        actions: [PageRefreshButton(onRefresh: _load)],
        title: const Text('المركبات'),
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
      ),
      floatingActionButton: _canEdit
          ? FloatingActionButton.extended(
              onPressed: _showForm,
              icon: const Icon(Icons.add),
              label: const Text('إضافة مركبة'),
            )
          : null,
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _visibleIndexes.isEmpty
          ? Center(
              child: Text(
                widget.recordId == null
                    ? 'لم تتم إضافة مركبات بعد'
                    : 'المركبة غير موجودة أو تم حذفها',
                style: TextStyle(color: Colors.white70, fontSize: 18),
              ),
            )
          : ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 90),
              itemCount: _visibleIndexes.length,
              separatorBuilder: (_, _) => const SizedBox(height: 12),
              itemBuilder: (context, visibleIndex) {
                final index = _visibleIndexes[visibleIndex];
                final vehicle = _vehicles[index];
                return Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFF172554),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Column(
                    children: [
                      Row(
                        children: [
                          if ((vehicle['imagePath']?.toString() ?? '')
                              .isNotEmpty)
                            ClipRRect(
                              borderRadius: BorderRadius.circular(10),
                              child: DocumentImage(
                                path: vehicle['imagePath'].toString(),
                                width: 62,
                                height: 62,
                                fit: BoxFit.cover,
                                errorBuilder: (_, _, _) => const Icon(
                                  Icons.directions_car,
                                  color: Color(0xFF38BDF8),
                                  size: 34,
                                ),
                              ),
                            )
                          else
                            const Icon(
                              Icons.directions_car,
                              color: Color(0xFF38BDF8),
                              size: 34,
                            ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  vehicle['name'] as String,
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 16,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                if ((vehicle['plate'] as String).isNotEmpty)
                                  Text(
                                    'اللوحة: ${vehicle['plate']}',
                                    style: const TextStyle(
                                      color: Colors.white60,
                                    ),
                                  ),
                                if ((vehicle['assignedEmployee']?.toString() ??
                                        '')
                                    .isNotEmpty)
                                  Text(
                                    'مع الموظف: ${vehicle['assignedEmployee']}',
                                    style: const TextStyle(
                                      color: Colors.white70,
                                    ),
                                  ),
                                Text(
                                  'الحالة: ${const {'working': 'تعمل', 'stopped': 'متوقفة', 'broken': 'عطلانة', 'maintenance': 'تحت الصيانة'}[vehicle['status']] ?? 'تعمل'}',
                                  style: TextStyle(
                                    color:
                                        vehicle['status'] == 'working' ||
                                            vehicle['status'] == null
                                        ? Colors.greenAccent
                                        : Colors.orangeAccent,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          if (_canEdit)
                            IconButton(
                              tooltip: 'تعديل',
                              onPressed: () => _showForm(index: index),
                              icon: const Icon(
                                Icons.edit_outlined,
                                color: Color(0xFF38BDF8),
                              ),
                            ),
                          if (_canDelete)
                            IconButton(
                              tooltip: 'حذف',
                              onPressed: () => _delete(index),
                              icon: const Icon(
                                Icons.delete_outline,
                                color: Color(0xFFEF4444),
                              ),
                            ),
                        ],
                      ),
                      const Divider(color: Colors.white12),
                      _dateRow(
                        'انتهاء الاستمارة',
                        vehicle['registration']?.toString() ?? '',
                      ),
                      _dateRow(
                        'انتهاء الفحص',
                        vehicle['inspection']?.toString() ?? '',
                      ),
                      _dateRow(
                        'انتهاء التأمين',
                        vehicle['insurance']?.toString() ?? '',
                      ),
                    ],
                  ),
                );
              },
            ),
    ),
  );
}

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  String _accountName = 'مستخدم خدووم';
  String _accountEmail = 'لم يُضف بريد إلكتروني';
  String _businessName = 'لم تُضف مؤسسة';
  bool _notificationsEnabled = true;
  bool _aiAlertsEnabled = true;
  bool _appointmentRemindersEnabled = true;
  String _language = 'العربية';
  bool _isLoading = true;
  bool _isAdmin = true;
  bool _canManageSettings = true;
  bool _canViewAuditLog = true;
  String? _profileImagePath;

  Future<void> _openTechnicalSupport() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (!mounted || token == null || token.isEmpty) return;
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    List<dynamic> tickets = [];
    try {
      tickets = await api.supportTickets();
    } catch (_) {}
    if (!mounted) {
      api.close();
      return;
    }
    final controller = TextEditingController();
    var selectedSupportCategory = 'مشكلة في الحساب';
    const supportCategories = [
      'مشكلة في الحساب',
      'تسجيل الدخول',
      'حظر الحساب أو الجهاز',
      'الاشتراك والباقة',
      'الشات',
      'المواعيد',
      'الأجهزة',
      'أخرى',
    ];
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) => AlertDialog(
          backgroundColor: const Color(0xFF172554),
          title: const Text(
            'الدعم الفني',
            style: TextStyle(color: Colors.white),
          ),
          content: SizedBox(
            width: 420,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: selectedSupportCategory,
                    dropdownColor: const Color(0xFF172554),
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'نوع المشكلة',
                      labelStyle: TextStyle(color: Colors.white70),
                      filled: true,
                      fillColor: Color(0xFF0B1020),
                    ),
                    items: supportCategories
                        .map(
                          (category) => DropdownMenuItem(
                            value: category,
                            child: Text(category),
                          ),
                        )
                        .toList(),
                    onChanged: (value) {
                      if (value != null)
                        setDialogState(() => selectedSupportCategory = value);
                    },
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: controller,
                    maxLines: 4,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'اشرح مشكلة الحساب أو الحظر',
                      labelStyle: TextStyle(color: Colors.white70),
                      filled: true,
                      fillColor: Color(0xFF0B1020),
                    ),
                  ),
                  const SizedBox(height: 12),
                  ...tickets.map((item) {
                    final x = Map<String, dynamic>.from(item as Map);
                    final status = x['status'] == 'resolved'
                        ? 'تم الحل'
                        : x['status'] == 'in_progress'
                        ? 'قيد المعالجة'
                        : 'طلب جديد';
                    return ListTile(
                      title: Text(
                        (x['category'] ?? 'طلب دعم').toString(),
                        style: const TextStyle(color: Colors.white),
                      ),
                      subtitle: Text(
                        '$status${(x['owner_reply'] ?? '').toString().isNotEmpty ? ' â€” ${x['owner_reply']}' : ''}',
                        style: const TextStyle(color: Colors.white70),
                      ),
                      trailing: x['status'] == 'resolved'
                          ? IconButton(
                              icon: const Icon(
                                Icons.delete_outline,
                                color: Colors.redAccent,
                              ),
                              tooltip: 'حذف الطلب',
                              onPressed: () async {
                                final confirmed = await showDialog<bool>(
                                  context: dialogContext,
                                  builder: (confirmContext) => AlertDialog(
                                    title: const Text('حذف الطلب؟'),
                                    content: const Text(
                                      'سيُحذف طلب الدعم المحلول من قائمتك.',
                                    ),
                                    actions: [
                                      TextButton(
                                        onPressed: () => Navigator.pop(
                                          confirmContext,
                                          false,
                                        ),
                                        child: const Text('إلغاء'),
                                      ),
                                      FilledButton(
                                        onPressed: () =>
                                            Navigator.pop(confirmContext, true),
                                        child: const Text('حذف'),
                                      ),
                                    ],
                                  ),
                                );
                                if (confirmed != true) return;
                                try {
                                  await api.deleteSupportTicket(
                                    int.parse(x['id'].toString()),
                                  );
                                  setDialogState(
                                    () => tickets.removeWhere(
                                      (item) =>
                                          (item as Map)['id'].toString() ==
                                          x['id'].toString(),
                                    ),
                                  );
                                  if (mounted)
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      const SnackBar(
                                        content: Text('تم حذف الطلب ✓'),
                                      ),
                                    );
                                } catch (e) {
                                  if (mounted)
                                    ScaffoldMessenger.of(context).showSnackBar(
                                      SnackBar(content: Text(e.toString())),
                                    );
                                }
                              },
                            )
                          : null,
                    );
                  }),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('إغلاق'),
            ),
            FilledButton(
              onPressed: () async {
                try {
                  await api.createSupportTicket(
                    category: selectedSupportCategory,
                    message: controller.text.trim(),
                  );
                  if (dialogContext.mounted) Navigator.pop(dialogContext);
                  if (mounted)
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('تم إرسال طلب الدعم ✓')),
                    );
                } catch (e) {
                  if (mounted)
                    ScaffoldMessenger.of(context)
                        .showSnackBar(SnackBar(content: Text(e.toString())));
                }
              },
              child: const Text('إرسال'),
            ),
          ],
        ),
      ),
    );
    api.close();
    Future<void>.delayed(const Duration(milliseconds: 350), controller.dispose);
  }

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await BranchPreferences.getInstance();
    final sessionType = prefs.getString('session_user_type') ?? 'admin';
    var canManageSettings = sessionType == 'admin';
    var canViewAuditLog = sessionType == 'admin';
    if (sessionType == 'employee') {
      final employeeId = prefs.getString('session_employee_id');
      final savedEmployees = prefs.getString('business_employees');
      if (employeeId != null && savedEmployees != null) {
        final employees = jsonDecode(savedEmployees) as List<dynamic>;
        for (final item in employees) {
          final employee = Map<String, dynamic>.from(item as Map);
          if (employee['id'].toString() == employeeId) {
            final permissions = Map<String, dynamic>.from(
              employee['permissions'] as Map? ?? {},
            );
            canManageSettings =
                permissions['manageSettings'] == true ||
                permissions['settings'] == true;
            canViewAuditLog = permissions['viewAuditLog'] == true;
            break;
          }
        }
      }
    }
    if (!mounted) return;
    setState(() {
      _accountName = sessionType == 'employee'
          ? prefs.getString('session_employee_name') ?? 'موظف خدووم'
          : prefs.getString('account_name') ?? 'مستخدم خدووم';
      _accountEmail = sessionType == 'employee'
          ? 'حساب موظف'
          : prefs.getString('account_email') ?? 'لم يُضف بريد إلكتروني';
      _businessName =
          prefs.getString('account_business_name') ?? 'لم تُضف مؤسسة';
      _notificationsEnabled = prefs.getBool('settings_notifications') ?? true;
      _aiAlertsEnabled = prefs.getBool('settings_ai_alerts') ?? true;
      _appointmentRemindersEnabled =
          prefs.getBool('settings_appointment_reminders') ?? true;
      _language = prefs.getString('settings_language') ?? 'العربية';
      _isAdmin = sessionType == 'admin';
      _canManageSettings = canManageSettings;
      _canViewAuditLog = canViewAuditLog;
      _profileImagePath = prefs.getString('profile_image_path');
      _isLoading = false;
    });
  }

  Future<void> _pickProfileImage() async {
    const channel = MethodChannel('khdoom/profile_image');
    final destinationPath = await channel.invokeMethod<String>('pickImage');
    if (destinationPath == null || destinationPath.isEmpty) return;
    final prefs = await BranchPreferences.getInstance();
    await prefs.setString('profile_image_path', destinationPath);
    if (!mounted) return;
    setState(() => _profileImagePath = destinationPath);
  }

  Future<bool> _ensureAdminPassword() async {
    if (!_isAdmin) return true;
    const storage = FlutterSecureStorage();
    final current = await storage.read(key: 'admin_account_password');
    if (current != null && current.isNotEmpty) return true;
    if (!mounted) return false;
    final controller = TextEditingController();
    final password = await showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: const Text('تعيين كلمة مرور المدير'),
        content: TextField(
          controller: controller,
          obscureText: true,
          decoration: const InputDecoration(
            labelText: 'كلمة مرور من 4 أحرف أو أرقام',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () {
              if (controller.text.length >= 4) {
                Navigator.pop(dialogContext, controller.text);
              }
            },
            child: const Text('حفظ'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (password == null) return false;
    await storage.write(key: 'admin_account_password', value: password);
    return true;
  }

  Future<void> _logout() async {
    if (!await _ensureAdminPassword()) return;
    final prefs = await BranchPreferences.getInstance();
    final sessionType = prefs.getString('session_user_type');
    if (sessionType == 'admin') {
      await prefs.setString(
        'remembered_login_username',
        prefs.getString('admin_login_username') ?? 'admin',
      );
    } else if (sessionType == 'employee') {
      final employeeId = prefs.getString('session_employee_id');
      final savedEmployees = prefs.getString('business_employees');
      if (employeeId != null && savedEmployees != null) {
        for (final item in jsonDecode(savedEmployees) as List) {
          final employee = Map<String, dynamic>.from(item as Map);
          if (employee['id'].toString() == employeeId) {
            await prefs.setString(
              'remembered_login_username',
              (employee['username'] ?? '').toString(),
            );
            break;
          }
        }
      }
    }
    await prefs.setBool('has_logged_out_once', true);
    await prefs.remove('session_user_type');
    await prefs.remove('session_employee_id');
    await prefs.remove('session_employee_name');
    await prefs.remove('session_employee_permissions');
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (_) => const LoginPage()),
      (_) => false,
    );
  }

  Future<void> _changeLoginCredentials() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final currentUsername = prefs.getString('admin_login_username') ?? 'admin';
    final usernameController = TextEditingController(text: currentUsername);
    final currentPasswordController = TextEditingController();
    final newPasswordController = TextEditingController();
    if (!mounted) return;
    final result = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: const Color(0xFF172554),
        title: const Text(
          'تغيير بيانات الدخول',
          style: TextStyle(color: Colors.white),
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: usernameController,
                autocorrect: false,
                style: const TextStyle(color: Colors.white),
                decoration: _credentialDecoration(
                  'اسم المستخدم الجديد',
                  Icons.person_outline,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: currentPasswordController,
                obscureText: true,
                style: const TextStyle(color: Colors.white),
                decoration: _credentialDecoration(
                  'كلمة المرور الحالية',
                  Icons.lock_outline,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: newPasswordController,
                obscureText: true,
                style: const TextStyle(color: Colors.white),
                decoration: _credentialDecoration(
                  'كلمة المرور الجديدة (اختياري)',
                  Icons.password_outlined,
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, {
              'username': usernameController.text.trim().toLowerCase(),
              'currentPassword': currentPasswordController.text,
              'newPassword': newPasswordController.text,
            }),
            child: const Text('حفظ'),
          ),
        ],
      ),
    );
    usernameController.dispose();
    currentPasswordController.dispose();
    newPasswordController.dispose();
    if (result == null || !mounted) return;
    final savedPassword = await storage.read(key: 'admin_account_password');
    if (!mounted) return;
    if (savedPassword != result['currentPassword']) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('كلمة المرور الحالية غير صحيحة')),
      );
      return;
    }
    final newUsername = result['username'] ?? '';
    if (newUsername.length < 3) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('اسم المستخدم يجب أن يكون 3 أحرف على الأقل'),
        ),
      );
      return;
    }
    final employeesJson = prefs.getString('business_employees');
    if (employeesJson != null) {
      final used = (jsonDecode(employeesJson) as List).any(
        (item) =>
            (Map<String, dynamic>.from(item as Map)['username'] ?? '')
                .toString()
                .toLowerCase() ==
            newUsername,
      );
      if (used) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('اسم المستخدم مستخدم لأحد الموظفين')),
        );
        return;
      }
    }
    final newPassword = result['newPassword'] ?? '';
    if (newPassword.isNotEmpty && newPassword.length < 4) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('كلمة المرور الجديدة 4 خانات على الأقل')),
      );
      return;
    }
    await prefs.setString('admin_login_username', newUsername);
    await prefs.setString('remembered_login_username', newUsername);
    if (newPassword.isNotEmpty) {
      await storage.write(key: 'admin_account_password', value: newPassword);
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('تم تحديث اسم المستخدم وكلمة المرور')),
    );
  }

  InputDecoration _credentialDecoration(String label, IconData icon) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white70),
      prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
      filled: true,
      fillColor: const Color(0xFF0B1020),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide.none,
      ),
    );
  }

  Future<void> _saveBool(String key, bool value) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setBool(key, value);
  }

  Future<void> _saveLanguage(String language) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setString('settings_language', language);
    if (!mounted) return;
    setState(() => _language = language);
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('تم حفظ اللغة المفضلة.')));
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [PageRefreshButton(onRefresh: _loadSettings)],
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('الإعدادات'),
        ),
        body: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(18),
                      border: Border.all(color: const Color(0xFF38BDF8)),
                    ),
                    child: Row(
                      children: [
                        GestureDetector(
                          onTap: _isAdmin ? _pickProfileImage : null,
                          child: Stack(
                            clipBehavior: Clip.none,
                            children: [
                              Container(
                                width: 64,
                                height: 64,
                                decoration: const BoxDecoration(
                                  color: Color(0xFF0C4A6E),
                                  shape: BoxShape.circle,
                                ),
                                clipBehavior: Clip.antiAlias,
                                child:
                                    _profileImagePath != null &&
                                        File(_profileImagePath!).existsSync()
                                    ? Image.file(
                                        File(_profileImagePath!),
                                        fit: BoxFit.cover,
                                      )
                                    : const Icon(
                                        Icons.person_outline,
                                        color: Color(0xFF7DD3FC),
                                        size: 34,
                                      ),
                              ),
                              const Positioned(
                                left: -2,
                                bottom: -2,
                                child: CircleAvatar(
                                  radius: 11,
                                  backgroundColor: Color(0xFF38BDF8),
                                  child: Icon(
                                    Icons.camera_alt,
                                    size: 13,
                                    color: Color(0xFF0B1020),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _accountName,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 16,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                _accountEmail,
                                style: const TextStyle(color: Colors.white60),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                _businessName,
                                style: const TextStyle(
                                  color: Color(0xFF7DD3FC),
                                ),
                              ),
                              const SizedBox(height: 3),
                              const Text(
                                'اضغط على الصورة لتغييرها',
                                style: TextStyle(
                                  color: Colors.white38,
                                  fontSize: 11,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 22),
                  if (_canManageSettings) ...[
                    _sectionTitle('الإشعارات'),
                    const SizedBox(height: 10),
                    _settingsSwitch(
                      icon: Icons.notifications_outlined,
                      title: 'الإشعارات العامة',
                      subtitle: 'تشغيل أو إيقاف جميع تنبيهات خدووم',
                      value: _notificationsEnabled,
                      onChanged: (value) async {
                        await _saveBool('settings_notifications', value);
                        await KhdoomNotifications.syncStoredAlerts();
                        if (!mounted) return;
                        setState(() => _notificationsEnabled = value);
                      },
                    ),
                    const SizedBox(height: 10),
                    _settingsSwitch(
                      icon: Icons.smart_toy_outlined,
                      title: 'تنبيهات موظفي AI',
                      subtitle: 'تنبيه عند طلب تدخل موظف بشري',
                      value: _aiAlertsEnabled,
                      enabled: _notificationsEnabled,
                      onChanged: (value) async {
                        await _saveBool('settings_ai_alerts', value);
                        if (!mounted) return;
                        setState(() => _aiAlertsEnabled = value);
                      },
                    ),
                    const SizedBox(height: 10),
                    _settingsSwitch(
                      icon: Icons.event_available_outlined,
                      title: 'تذكير المواعيد',
                      subtitle: 'إشعار قبل المعاينات والمواعيد المسجلة',
                      value: _appointmentRemindersEnabled,
                      enabled: _notificationsEnabled,
                      onChanged: (value) async {
                        await _saveBool(
                          'settings_appointment_reminders',
                          value,
                        );
                        await KhdoomNotifications.syncStoredAlerts();
                        if (!mounted) return;
                        setState(() => _appointmentRemindersEnabled = value);
                      },
                    ),
                    const SizedBox(height: 22),
                    _sectionTitle('التطبيق'),
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                      decoration: BoxDecoration(
                        color: const Color(0xFF172554),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: DropdownButtonFormField<String>(
                        initialValue: _language,
                        dropdownColor: const Color(0xFF172554),
                        style: const TextStyle(color: Colors.white),
                        decoration: const InputDecoration(
                          labelText: 'اللغة',
                          labelStyle: TextStyle(color: Colors.white70),
                          prefixIcon: Icon(
                            Icons.language,
                            color: Color(0xFF7DD3FC),
                          ),
                          border: InputBorder.none,
                        ),
                        items: const [
                          DropdownMenuItem(
                            value: 'العربية',
                            child: Text('العربية'),
                          ),
                          DropdownMenuItem(
                            value: 'English',
                            enabled: false,
                            child: Text('English — قريبًا'),
                          ),
                        ],
                        onChanged: (value) {
                          if (value == null) return;
                          _saveLanguage(value);
                        },
                      ),
                    ),
                    const SizedBox(height: 10),
                  ],
                  if (_isAdmin) ...[
                    const HomeBranchesSwitch(),
                    _settingsInfoTile(
                      icon: Icons.account_tree_outlined,
                      title: 'إدارة الفروع',
                      subtitle: 'التبديل بين الفروع أو إضافة فرع مستقل',
                      onTap: () async {
                        final changed = await Navigator.push<bool>(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const BranchManagementPage(),
                          ),
                        );
                        // Leave settings captured for the previous branch.
                        // Dashboard reloads its branch after settings returns.
                        if (mounted && changed == true)
                          Navigator.of(context).pop();
                      },
                    ),
                    const SizedBox(height: 10),
                    _settingsInfoTile(
                      icon: Icons.key_outlined,
                      title: 'اسم المستخدم وكلمة المرور',
                      subtitle: 'تغيير بيانات دخول المدير',
                      onTap: _changeLoginCredentials,
                    ),
                    const SizedBox(height: 10),
                    _settingsInfoTile(
                      icon: Icons.workspace_premium_outlined,
                      title: 'الباقات والاشتراك',
                      subtitle: 'المجانية والأساسية وVIP',
                      onTap: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const SubscriptionPackagesPage(),
                          ),
                        );
                      },
                    ),
                    const SizedBox(height: 10),
                    _settingsInfoTile(
                      icon: Icons.manage_accounts_outlined,
                      title: 'الموظفون والصلاحيات',
                      subtitle: 'إضافة الموظفين وتحديد ما يمكنهم الوصول إليه',
                      onTap: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const EmployeesPermissionsPage(),
                          ),
                        );
                      },
                    ),
                    const SizedBox(height: 10),
                    _settingsInfoTile(
                      icon: Icons.security_outlined,
                      title: 'الخصوصية والأمان',
                      subtitle: 'كلمات المرور والمفاتيح السرية لا تُحفظ محليًا',
                      onTap: () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const PrivacySecurityPage(),
                          ),
                        );
                      },
                    ),
                    const SizedBox(height: 10),
                  ],
                  if (_canViewAuditLog) ...[
                    _settingsInfoTile(
                      icon: Icons.history_outlined,
                      title: 'سجل العمليات',
                      subtitle:
                          'الدخول والتعديلات والعمليات المهمة داخل المؤسسة',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => const AuditLogPage()),
                      ),
                    ),
                    const SizedBox(height: 10),
                  ],
                  _settingsInfoTile(
                    icon: Icons.support_agent_outlined,
                    title: 'الدعم الفني',
                    subtitle: 'إرسال مشكلة ومتابعة رد دعم خدووم',
                    onTap: _openTechnicalSupport,
                  ),
                  const SizedBox(height: 10),
                  _settingsInfoTile(
                    icon: Icons.info_outline,
                    title: 'حول خدووم',
                    subtitle: 'الإصدار 1.0.0',
                  ),
                  const SizedBox(height: 10),
                  _settingsInfoTile(
                    icon: Icons.logout,
                    title: 'تسجيل الخروج',
                    subtitle: 'الخروج من الحساب الحالي',
                    onTap: _logout,
                  ),
                ],
              ),
      ),
    );
  }

  Widget _sectionTitle(String title) {
    return Text(
      title,
      style: const TextStyle(
        color: Colors.white,
        fontSize: 18,
        fontWeight: FontWeight.bold,
      ),
    );
  }

  Widget _settingsSwitch({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
    bool enabled = true,
  }) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          Icon(icon, color: enabled ? const Color(0xFF7DD3FC) : Colors.white24),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    color: enabled ? Colors.white : Colors.white38,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  subtitle,
                  style: TextStyle(
                    color: enabled ? Colors.white60 : Colors.white24,
                  ),
                ),
              ],
            ),
          ),
          Switch(
            value: value,
            activeThumbColor: const Color(0xFF38BDF8),
            onChanged: enabled ? onChanged : null,
          ),
        ],
      ),
    );
  }

  Widget _settingsInfoTile({
    required IconData icon,
    required String title,
    required String subtitle,
    VoidCallback? onTap,
  }) {
    return Material(
      color: const Color(0xFF172554),
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              Icon(icon, color: const Color(0xFF7DD3FC)),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: const TextStyle(color: Colors.white60),
                    ),
                  ],
                ),
              ),
              if (onTap != null)
                const Icon(
                  Icons.arrow_back_ios_new,
                  color: Color(0xFF38BDF8),
                  size: 17,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class SubscriptionPackagesPage extends StatefulWidget {
  final String promotionCode;

  const SubscriptionPackagesPage({super.key, this.promotionCode = ''});

  @override
  State<SubscriptionPackagesPage> createState() =>
      _SubscriptionPackagesPageState();
}

class _SubscriptionPackagesPageState extends State<SubscriptionPackagesPage> {
  String _currentPackage = 'free';
  bool _loading = true;
  List<Map<String, dynamic>> _packageOffers = [];
  Map<String, dynamic> _paymentSettings = {};
  PackageResourceLimits _resourceLimits = PackageResourceLimits.defaults;

  static const _packages = [
    {
      'id': 'free',
      'name': 'المجانية',
      'price': 'مجانًا',
      'description': 'للبدء وتجربة خدووم',
      'features': [
        'مستخدم واحد',
        'مركبة واحدة',
        'اسأل موظف AI',
        'التعليم الأساسي',
        'التنبيهات الأساسية',
      ],
      'color': 0xFF64748B,
    },
    {
      'id': 'basic',
      'name': 'الأساسية',
      'price': '49 ريال / شهر',
      'priceValue': 49,
      'description': 'للمؤسسات الصغيرة والمتوسطة',
      'features': [
        'حتى 5 موظفين',
        'حتى 5 مركبات',
        'اسأل موظف AI وموظف الاستقبال',
        'جميع التنبيهات والصلاحيات',
      ],
      'color': 0xFF0284C7,
    },
    {
      'id': 'vip',
      'name': 'VIP',
      'price': '99 ريال / شهر',
      'priceValue': 99,
      'description': 'لإدارة المؤسسة دون حدود',
      'features': [
        'موظفون ومركبات بلا حد',
        'جميع مزايا الذكاء الاصطناعي',
        'إنشاء إعلان للمؤسسة',
        'تقارير متقدمة',
        'دعم أولوية VIP',
      ],
      'color': 0xFFF59E0B,
    },
  ];

  @override
  void initState() {
    super.initState();
    _loadPackage();
  }

  Future<void> _loadPackage() async {
    final prefs = await BranchPreferences.getInstance();
    final resourceLimitsFuture = PackageResourceLimits.load(prefs);
    var offers = <Map<String, dynamic>>[];
    var paymentSettings = <String, dynamic>{};
    var currentPackage = prefs.getString('subscription_package') ?? 'free';
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    try {
      offers = (await api.packageOffers())
          .map((item) => Map<String, dynamic>.from(item as Map))
          .toList();
    } catch (_) {
      offers = [];
    }
    try {
      api.token = await const FlutterSecureStorage().read(
        key: 'cloud_session_token',
      );
      if (api.token != null) {
        final accountRequests = await Future.wait<dynamic>([
          api.paymentSettings(),
          api.subscription(),
        ]);
        paymentSettings = Map<String, dynamic>.from(accountRequests[0] as Map);
        final subscription = Map<String, dynamic>.from(
          accountRequests[1] as Map,
        );
        final serverPackage = subscription['package']?.toString();
        if (['free', 'basic', 'vip'].contains(serverPackage)) {
          currentPackage = serverPackage!;
          await prefs.setString('subscription_package', currentPackage);
        }
      }
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('تعذر جلب الباقة؛ المعروض آخر بيانات محفوظة'),
          ),
        );
    } finally {
      api.close();
    }
    final resourceLimits = await resourceLimitsFuture;
    if (!mounted) return;
    setState(() {
      _currentPackage = currentPackage;
      _packageOffers = offers;
      _paymentSettings = paymentSettings;
      _resourceLimits = resourceLimits;
      _loading = false;
    });
  }

  Future<void> _selectPackage(Map<String, Object> package) async {
    final id = package['id'] as String;

    final prefs = await BranchPreferences.getInstance();
    if (!mounted) return;
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    const storage = FlutterSecureStorage();
    final cloudToken = await storage.read(key: 'cloud_session_token');
    if (!mounted) {
      api.close();
      return;
    }
    if (cloudToken == null || cloudToken.isEmpty) {
      api.close();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('سجل الدخول أولًا لإرسال طلب الترقية.')),
      );
      return;
    }
    api.token = cloudToken;
    if (id == 'free') {
      try {
        final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('الرجوع إلى الباقة المجانية'),
            content: const Text(
              'سيصل طلبك إلى الدعم في لوحة الإدارة. تبقى باقتك الحالية حتى اعتماد المالك. بعد الاعتماد تتوقف المزايا المدفوعة وتطبق حدود المجانية، بدون حذف سجلاتك.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext, true),
                child: const Text('إرسال الطلب'),
              ),
            ],
          ),
        );
        if (confirmed != true) return;
        await api.createSupportTicket(
          category: 'تغيير الباقة',
          message:
              'أطلب الرجوع من الباقة $_currentPackage إلى الباقة المجانية. أوافق على تطبيق حدود المجانية وإيقاف المزايا المدفوعة بعد اعتماد المالك، مع الاحتفاظ بسجلات المؤسسة.',
        );
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('تم إرسال طلب المجانية إلى الدعم في لوحة الإدارة'),
          ),
        );
      } catch (_) {
        if (mounted)
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('تعذر إرسال طلب المجانية، حاول مرة أخرى'),
            ),
          );
      } finally {
        api.close();
      }
      return;
    }
    var discountCode = widget.promotionCode;
    final transferNameController = TextEditingController();
    String transferReceipt = '';
    Map<String, dynamic>? discountResult;
    String? discountError;
    var applyingDiscount = false;
    var requestSent = false;
    final availableOffers = _packageOffers
        .where((offer) => offer['package']?.toString() == id)
        .toList();
    Map<String, dynamic>? selectedOffer = availableOffers.isEmpty
        ? null
        : availableOffers.first;

    if (_paymentSettings.isEmpty) {
      try {
        _paymentSettings = await api.paymentSettings();
      } on CloudApiException catch (error) {
        api.close();
        transferNameController.dispose();
        if (mounted)
          ScaffoldMessenger.of(context)
              .showSnackBar(SnackBar(content: Text(error.message)));
        return;
      }
    }

    try {
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => StatefulBuilder(
          builder: (dialogContext, setDialogState) => AlertDialog(
            title: Text('باقة ${package['name']}'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (availableOffers.isEmpty)
                    Text('السعر: ${package['price']}')
                  else
                    DropdownButtonFormField<int>(
                      isExpanded: true,
                      alignment: Alignment.centerRight,
                      initialValue: selectedOffer?['id'] as int?,
                      decoration: const InputDecoration(
                        labelText: 'اختر مدة وعرض الاشتراك',
                        prefixIcon: Icon(Icons.calendar_month_outlined),
                      ),
                      items: availableOffers.map((offer) {
                        return DropdownMenuItem<int>(
                          value: offer['id'] as int,
                          alignment: Alignment.centerRight,
                          child: Text(
                            '${offer['paid_months']} شهر — ${offer['price_sar']} ريال',
                            textDirection: TextDirection.rtl,
                            textAlign: TextAlign.right,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        );
                      }).toList(),
                      onChanged: (value) => setDialogState(() {
                        selectedOffer = availableOffers.firstWhere(
                          (offer) => offer['id'] == value,
                        );
                        discountResult = null;
                      }),
                    ),
                  if (selectedOffer != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        '${selectedOffer!['paid_months']} شهر — ${selectedOffer!['price_sar']} ريال${selectedOffer!['bonus_months'] == 0 ? '' : ' + ${selectedOffer!['bonus_months']} شهر مجانًا'}',
                      ),
                    ),
                  const SizedBox(height: 14),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFFEFF6FF),
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'بيانات التحويل البنكي',
                          style: TextStyle(
                            color: Color(0xFF1E3A8A),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        Text(
                          'البنك: ${_paymentSettings['bank_name'] ?? ''}',
                          style: const TextStyle(color: Color(0xFF1E3A8A)),
                        ),
                        Text(
                          'اسم الحساب: ${_paymentSettings['account_name'] ?? ''}',
                          style: const TextStyle(color: Color(0xFF1E3A8A)),
                        ),
                        SelectableText(
                          'الآيبان: ${_paymentSettings['iban'] ?? ''}',
                          style: const TextStyle(
                            color: Color(0xFF1E3A8A),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        if ((_paymentSettings['account_number'] ?? '')
                            .toString()
                            .isNotEmpty)
                          Text(
                            'رقم الحساب: ${_paymentSettings['account_number']}',
                            style: const TextStyle(color: Color(0xFF1E3A8A)),
                          ),
                        if ((_paymentSettings['instructions'] ?? '')
                            .toString()
                            .isNotEmpty)
                          Text(
                            _paymentSettings['instructions'].toString(),
                            style: const TextStyle(color: Color(0xFF1E3A8A)),
                          ),
                        TextButton.icon(
                          onPressed: () async {
                            await Clipboard.setData(
                              ClipboardData(
                                text: (_paymentSettings['iban'] ?? '')
                                    .toString(),
                              ),
                            );
                            if (dialogContext.mounted)
                              ScaffoldMessenger.of(dialogContext).showSnackBar(
                                const SnackBar(content: Text('تم نسخ الآيبان')),
                              );
                          },
                          icon: const Icon(Icons.copy),
                          label: const Text('نسخ الآيبان'),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: transferNameController,
                    decoration: const InputDecoration(
                      labelText: 'اسم المحوّل كما يظهر في البنك',
                      prefixIcon: Icon(Icons.person_outline),
                    ),
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: applyingDiscount
                        ? null
                        : () async {
                            final image = await ImagePicker().pickImage(
                              source: ImageSource.gallery,
                              imageQuality: 65,
                              maxWidth: 1400,
                              maxHeight: 1400,
                            );
                            if (image == null) return;
                            final bytes = await image.readAsBytes();
                            if (bytes.length > 850000) {
                              setDialogState(() {
                                discountError =
                                    'صورة الإيصال كبيرة؛ اختر صورة أصغر';
                              });
                              return;
                            }
                            setDialogState(() {
                              transferReceipt =
                                  'data:image/jpeg;base64,${base64Encode(bytes)}';
                              discountError = null;
                            });
                          },
                    icon: Icon(
                      transferReceipt.isEmpty
                          ? Icons.attach_file
                          : Icons.check_circle,
                    ),
                    label: Text(
                      transferReceipt.isEmpty
                          ? 'إرفاق صورة وصل التحويل (مطلوب)'
                          : 'تم إرفاق الوصل — تغييره',
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Expanded(
                        child: TextFormField(
                          initialValue: discountCode,
                          onChanged: (value) => discountCode = value,
                          textCapitalization: TextCapitalization.characters,
                          autocorrect: false,
                          decoration: const InputDecoration(
                            labelText: 'كود الخصم (اختياري)',
                            prefixIcon: Icon(Icons.discount_outlined),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      FilledButton(
                        onPressed: applyingDiscount
                            ? null
                            : () async {
                                final code = discountCode.trim();
                                if (code.isEmpty) {
                                  setDialogState(() {
                                    discountResult = null;
                                    discountError = 'اكتب كود الخصم أولًا';
                                  });
                                  return;
                                }
                                setDialogState(() {
                                  applyingDiscount = true;
                                  discountError = null;
                                });
                                try {
                                  final result = await api.previewDiscountCode(
                                    code,
                                    id,
                                  );
                                  if (!dialogContext.mounted) return;
                                  setDialogState(() {
                                    discountResult = result;
                                    discountError = null;
                                  });
                                } on CloudApiException catch (error) {
                                  if (!dialogContext.mounted) return;
                                  setDialogState(() {
                                    discountResult = null;
                                    discountError = error.message;
                                  });
                                } on SocketException {
                                  if (!dialogContext.mounted) return;
                                  setDialogState(() {
                                    discountResult = null;
                                    discountError = 'تعذر الاتصال بخادم خدووم';
                                  });
                                } finally {
                                  if (dialogContext.mounted) {
                                    setDialogState(
                                      () => applyingDiscount = false,
                                    );
                                  }
                                }
                              },
                        child: applyingDiscount
                            ? const SizedBox.square(
                                dimension: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Text('تطبيق'),
                      ),
                    ],
                  ),
                  if (discountError != null) ...[
                    const SizedBox(height: 10),
                    Text(
                      discountError!,
                      style: const TextStyle(color: Colors.redAccent),
                    ),
                  ],
                  if (discountResult != null) ...[
                    const SizedBox(height: 14),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFFDCFCE7),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Text(
                        'تم تطبيق خصم ${discountResult!['discountPercent']}% ✓\n'
                        'السعر الأصلي: ${discountResult!['originalPrice']} ريال\n'
                        'السعر بعد الخصم: ${discountResult!['discountedPrice']} ريال / شهر',
                        style: const TextStyle(
                          color: Color(0xFF166534),
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  const Text('سيصل طلب الترقية إلى إدارة خدووم للمراجعة.'),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: applyingDiscount
                    ? null
                    : () async {
                        if (transferReceipt.isEmpty) {
                          setDialogState(() {
                            discountError =
                                'يجب إرفاق صورة وصل التحويل قبل إرسال الطلب';
                          });
                          return;
                        }
                        setDialogState(() {
                          applyingDiscount = true;
                          discountError = null;
                        });
                        try {
                          await api.requestSubscription(
                            package: id,
                            transferName: transferNameController.text,
                            transferReceipt: transferReceipt,
                            discountCode: discountCode,
                            offerId: int.tryParse(
                              selectedOffer?['id']?.toString() ?? '',
                            ),
                          );
                          requestSent = true;
                          if (dialogContext.mounted) {
                            setDialogState(() => applyingDiscount = false);
                            Navigator.pop(dialogContext);
                          }
                        } on CloudApiException catch (error) {
                          if (dialogContext.mounted) {
                            setDialogState(() => discountError = error.message);
                          }
                        } on SocketException {
                          if (dialogContext.mounted) {
                            setDialogState(
                              () => discountError = 'تعذر الاتصال بخادم خدووم',
                            );
                          }
                        } finally {
                          if (dialogContext.mounted && !requestSent) {
                            setDialogState(() => applyingDiscount = false);
                          }
                        }
                      },
                child: const Text('حوّلت المبلغ — إرسال طلب التفعيل'),
              ),
            ],
          ),
        ),
      );
    } finally {
      api.close();
      transferNameController.dispose();
    }
    if (!mounted) return;
    if (requestSent) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'تم إرسال تنبيه التحويل وطلب التفعيل إلى إدارة خدووم ✓',
          ),
        ),
      );
    }
  }

  Future<void> _manageVipAdvertisement() async {
    await Navigator.push<void>(
      context,
      MaterialPageRoute(builder: (_) => const MyAdvertisementsPage()),
    );
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        actions: [PageRefreshButton(onRefresh: _loadPackage)],
        title: const Text('الباقات والاشتراك'),
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
      ),
      floatingActionButton: _currentPackage == 'vip'
          ? FloatingActionButton.extended(
              onPressed: _manageVipAdvertisement,
              backgroundColor: const Color(0xFFF59E0B),
              foregroundColor: const Color(0xFF0B1020),
              icon: const Icon(Icons.campaign),
              label: const Text('إدارة إعلاني'),
            )
          : null,
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView.separated(
              padding: const EdgeInsets.all(16),
              itemCount: _packages.length,
              separatorBuilder: (_, _) => const SizedBox(height: 14),
              itemBuilder: (context, index) {
                final package = _packages[index];
                final active = _currentPackage == package['id'];
                final color = Color(package['color'] as int);
                final features = List<String>.from(package['features'] as List);
                final packageId = package['id'] as String;
                if (packageId != 'vip') {
                  features[0] =
                      'حتى ${_resourceLimits.limit(packageId, 'employees')} موظفين';
                  features[1] =
                      'حتى ${_resourceLimits.limit(packageId, 'vehicles')} مركبات';
                }
                return Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: const Color(0xFF172554),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(
                      color: active ? color : Colors.white12,
                      width: active ? 2 : 1,
                    ),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          CircleAvatar(
                            backgroundColor: color.withValues(alpha: 0.18),
                            child: Icon(Icons.workspace_premium, color: color),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  package['name'] as String,
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 21,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                                Text(
                                  package['description'] as String,
                                  style: const TextStyle(color: Colors.white60),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                      if (active)
                        Chip(
                          label: const Text('باقتك الحالية'),
                          backgroundColor: color,
                        ),
                      const SizedBox(height: 16),
                      Text(
                        package['price'] as String,
                        style: TextStyle(
                          color: color,
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 12),
                      ...features.map(
                        (feature) => Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Row(
                            children: [
                              const Icon(
                                Icons.check_circle,
                                color: Color(0xFF22C55E),
                                size: 20,
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  feature,
                                  style: const TextStyle(color: Colors.white70),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 10),
                      SizedBox(
                        width: double.infinity,
                        child: FilledButton(
                          onPressed: active
                              ? null
                              : () => _selectPackage(package),
                          style: FilledButton.styleFrom(backgroundColor: color),
                          child: Text(
                            active
                                ? 'الباقة مفعلة'
                                : packageId == 'free'
                                ? 'طلب الرجوع للمجانية'
                                : 'عرض السعر وكود الخصم',
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
    ),
  );
}

class PrivacySecurityPage extends StatefulWidget {
  const PrivacySecurityPage({super.key});

  @override
  State<PrivacySecurityPage> createState() => _PrivacySecurityPageState();
}

class _PrivacySecurityPageState extends State<PrivacySecurityPage> {
  static const _secureStorage = FlutterSecureStorage();
  bool _appLockEnabled = false;
  bool _hideCustomerData = false;
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadSecuritySettings();
  }

  Future<void> _loadSecuritySettings() async {
    final prefs = await BranchPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _appLockEnabled = prefs.getBool('security_app_lock') ?? false;
      _hideCustomerData = prefs.getBool('security_hide_customer_data') ?? false;
      _isLoading = false;
    });
  }

  Future<String?> _requestPin({required bool confirmPin}) async {
    final pinController = TextEditingController();
    final confirmController = TextEditingController();
    final formKey = GlobalKey<FormState>();
    final result = await showDialog<String>(
      context: context,
      builder: (dialogContext) {
        return Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            backgroundColor: const Color(0xFF172554),
            title: Text(
              confirmPin ? 'إنشاء رمز قفل' : 'أدخل رمز القفل',
              style: const TextStyle(color: Colors.white),
            ),
            content: Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextFormField(
                    controller: pinController,
                    obscureText: true,
                    maxLength: 6,
                    keyboardType: TextInputType.number,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'رمز PIN من 4 إلى 6 أرقام',
                      labelStyle: TextStyle(color: Colors.white70),
                    ),
                    validator: (value) {
                      final pin = value ?? '';
                      return !RegExp(r'^\d{4,6}$').hasMatch(pin)
                          ? 'استخدم 4 إلى 6 أرقام'
                          : null;
                    },
                  ),
                  if (confirmPin)
                    TextFormField(
                      controller: confirmController,
                      obscureText: true,
                      maxLength: 6,
                      keyboardType: TextInputType.number,
                      style: const TextStyle(color: Colors.white),
                      decoration: const InputDecoration(
                        labelText: 'تأكيد رمز PIN',
                        labelStyle: TextStyle(color: Colors.white70),
                      ),
                      validator: (value) => value != pinController.text
                          ? 'رمزا القفل غير متطابقين'
                          : null,
                    ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () {
                  if (!formKey.currentState!.validate()) return;
                  Navigator.pop(dialogContext, pinController.text);
                },
                child: const Text('تأكيد'),
              ),
            ],
          ),
        );
      },
    );
    pinController.dispose();
    confirmController.dispose();
    return result;
  }

  Future<void> _toggleAppLock(bool value) async {
    final prefs = await BranchPreferences.getInstance();
    if (value) {
      final pin = await _requestPin(confirmPin: true);
      if (pin == null) return;
      await _secureStorage.write(key: 'security_app_pin', value: pin);
      await prefs.setBool('security_app_lock', true);
      if (!mounted) return;
      setState(() => _appLockEnabled = true);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تم تفعيل قفل خدووم عند فتح التطبيق.')),
      );
      return;
    }

    final currentPin = await _requestPin(confirmPin: false);
    if (currentPin == null) return;
    final savedPin = await _secureStorage.read(key: 'security_app_pin');
    if (currentPin != savedPin) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('رمز القفل غير صحيح.')));
      return;
    }
    await _secureStorage.delete(key: 'security_app_pin');
    await prefs.setBool('security_app_lock', false);
    if (!mounted) return;
    setState(() => _appLockEnabled = false);
  }

  Future<void> _toggleHideCustomerData(bool value) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setBool('security_hide_customer_data', value);
    if (!mounted) return;
    setState(() => _hideCustomerData = value);
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [PageRefreshButton(onRefresh: _loadSecuritySettings)],
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('الخصوصية والأمان'),
        ),
        body: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: const Color(0xFF22C55E)),
                    ),
                    child: const Row(
                      children: [
                        Icon(
                          Icons.verified_user_outlined,
                          color: Color(0xFF4ADE80),
                          size: 34,
                        ),
                        SizedBox(width: 14),
                        Expanded(
                          child: Text(
                            'رمز القفل يُحفظ في التخزين الآمن للنظام، ولا تُحفظ كلمات مرور الحساب داخل التطبيق.',
                            style: TextStyle(
                              color: Colors.white70,
                              height: 1.5,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 18),
                  _securitySwitch(
                    icon: Icons.lock_outline,
                    title: 'قفل التطبيق برمز PIN',
                    subtitle: 'طلب رمز القفل عند تشغيل خدووم من جديد',
                    value: _appLockEnabled,
                    onChanged: _toggleAppLock,
                  ),
                  const SizedBox(height: 10),
                  _securitySwitch(
                    icon: Icons.visibility_off_outlined,
                    title: 'إخفاء بيانات العملاء الحساسة',
                    subtitle: 'إخفاء أجزاء من أرقام الجوال في واجهات العملاء',
                    value: _hideCustomerData,
                    onChanged: _toggleHideCustomerData,
                  ),
                  const SizedBox(height: 10),
                  ListTile(
                    tileColor: const Color(0xFF172554),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                    leading: const Icon(
                      Icons.devices_outlined,
                      color: Color(0xFF7DD3FC),
                    ),
                    title: const Text(
                      'الأجهزة والجلسات',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    subtitle: const Text(
                      'الأجهزة الموثوقة وفصل الجلسات المشبوهة',
                      style: TextStyle(color: Colors.white60),
                    ),
                    trailing: const Icon(
                      Icons.chevron_left,
                      color: Colors.white54,
                    ),
                    onTap: () => Navigator.push(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const TrustedDevicesPage(),
                      ),
                    ),
                  ),
                ],
              ),
      ),
    );
  }

  Widget _securitySwitch({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          Icon(icon, color: const Color(0xFF7DD3FC)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 4),
                Text(subtitle, style: const TextStyle(color: Colors.white60)),
              ],
            ),
          ),
          Switch(
            value: value,
            activeThumbColor: const Color(0xFF38BDF8),
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }
}

class SecurityAlertsPage extends StatefulWidget {
  const SecurityAlertsPage({super.key});
  @override
  State<SecurityAlertsPage> createState() => _SecurityAlertsPageState();
}

class _SecurityAlertsPageState extends State<SecurityAlertsPage> {
  static const _actions = {
    'failed_login',
    'suspicious_login',
    'new_device',
    'employee_updated',
    'employee_deleted',
    'device_trust_updated',
    'device_disconnected',
    'device_blocked',
    'device_unblocked',
    'blocked_device_login',
    'sessions_disconnected',
  };
  List<Map<String, dynamic>> _alerts = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'سجل الدخول لعرض التنبيهات الأمنية';
        });
      return;
    }
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      final rows = await api.auditLogs();
      final alerts = rows
          .map((e) => Map<String, dynamic>.from(e as Map))
          .where((e) => _actions.contains(e['action']))
          .toList();
      if (alerts.isNotEmpty)
        await prefs.setInt(
          'security_alert_last_seen_id',
          (alerts.first['id'] as num?)?.toInt() ?? 0,
        );
      if (mounted)
        setState(() {
          _alerts = alerts;
          _loading = false;
        });
    } on CloudApiException catch (e) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = e.message;
        });
    } catch (_) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'تعذر تحميل التنبيهات الأمنية';
        });
    } finally {
      api.close();
    }
  }

  IconData _icon(String action) =>
      action == 'failed_login' || action == 'suspicious_login'
      ? Icons.warning_amber_rounded
      : action == 'new_device'
      ? Icons.phonelink_lock_outlined
      : Icons.admin_panel_settings_outlined;

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
        title: const Text('التنبيهات الأمنية'),
        actions: [
          IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Text(
                _error!,
                style: const TextStyle(color: Colors.orangeAccent),
              ),
            )
          : _alerts.isEmpty
          ? const Center(
              child: Text(
                'لا توجد تنبيهات أمنية',
                style: TextStyle(color: Colors.white70),
              ),
            )
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: _alerts.length,
                separatorBuilder: (_, _) => const SizedBox(height: 10),
                itemBuilder: (_, index) {
                  final alert = _alerts[index];
                  final danger = {
                    'failed_login',
                    'suspicious_login',
                    'blocked_device_login',
                  }.contains(alert['action']);
                  return Container(
                    padding: const EdgeInsets.all(15),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(15),
                      border: Border.all(
                        color: danger
                            ? const Color(0xFFF59E0B)
                            : const Color(0xFF2563EB),
                      ),
                    ),
                    child: Row(
                      children: [
                        Icon(
                          _icon(alert['action']?.toString() ?? ''),
                          color: danger
                              ? const Color(0xFFFBBF24)
                              : const Color(0xFF7DD3FC),
                          size: 30,
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                (alert['summary'] ?? 'تنبيه أمني').toString(),
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                'بواسطة: ${(alert['actor_name'] ?? alert['actor_username'] ?? 'النظام')}',
                                style: const TextStyle(color: Colors.white60),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                (alert['created_at'] ?? '')
                                    .toString()
                                    .replaceFirst('T', ' ')
                                    .split('.')
                                    .first,
                                style: const TextStyle(
                                  color: Colors.white38,
                                  fontSize: 12,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
    ),
  );
}

class AuditLogPage extends StatefulWidget {
  const AuditLogPage({super.key});
  @override
  State<AuditLogPage> createState() => _AuditLogPageState();
}

class _AuditLogPageState extends State<AuditLogPage> {
  List<Map<String, dynamic>> _logs = [];
  bool _loading = true;
  String? _error;
  String _actorFilter = '';
  String _typeFilter = '';
  DateTime? _dateFilter;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'سجل الدخول لعرض سجل العمليات';
        });
      return;
    }
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      final rows = await api.auditLogs();
      if (mounted)
        setState(() {
          _logs = rows.map((e) => Map<String, dynamic>.from(e as Map)).toList();
          _loading = false;
        });
    } on CloudApiException catch (e) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = e.message;
        });
    } catch (_) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'تعذر تحميل سجل العمليات';
        });
    } finally {
      api.close();
    }
  }

  String _time(Object? value) {
    final date = DateTime.tryParse(value?.toString() ?? '')?.toLocal();
    if (date == null) return '';
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(date.day)}/${two(date.month)}/${date.year} â€” ${two(date.hour)}:${two(date.minute)}';
  }

  String _actor(Map<String, dynamic> log) =>
      (log['actor_name'] ?? log['actor_username'] ?? 'النظام').toString();

  List<String> get _actors =>
      _logs.map(_actor).toSet().toList()..sort((a, b) => a.compareTo(b));

  bool _matchesType(Map<String, dynamic> log) {
    if (_typeFilter.isEmpty) return true;
    final action = log['action']?.toString() ?? '';
    final target = log['target_type']?.toString() ?? '';
    return switch (_typeFilter) {
      'security' =>
        target == 'security' ||
            action.contains('login') ||
            action.contains('device') ||
            action.contains('session'),
      'employee' => target == 'employee' || action.startsWith('employee_'),
      'appointment' =>
        target == 'appointment' || action.startsWith('appointment_'),
      'organization' =>
        target == 'organization' || action.startsWith('organization_'),
      _ => true,
    };
  }

  List<Map<String, dynamic>> get _filteredLogs => _logs.where((log) {
    if (_actorFilter.isNotEmpty && _actor(log) != _actorFilter) return false;
    if (!_matchesType(log)) return false;
    if (_dateFilter != null) {
      final date = DateTime.tryParse(log['created_at']?.toString() ?? '')
          ?.toLocal();
      if (date == null ||
          date.year != _dateFilter!.year ||
          date.month != _dateFilter!.month ||
          date.day != _dateFilter!.day) {
        return false;
      }
    }
    return true;
  }).toList();

  Future<void> _pickDate() async {
    final selected = await showDatePicker(
      context: context,
      initialDate: _dateFilter ?? DateTime.now(),
      firstDate: DateTime(2024),
      lastDate: DateTime.now().add(const Duration(days: 1)),
    );
    if (selected != null && mounted) setState(() => _dateFilter = selected);
  }

  void _clearFilters() => setState(() {
    _actorFilter = '';
    _typeFilter = '';
    _dateFilter = null;
  });

  Widget _filters() => Container(
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: const Color(0xFF172554),
      borderRadius: BorderRadius.circular(15),
      border: Border.all(color: const Color(0xFF2563EB)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'فلترة سجل العمليات',
          style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 10),
        DropdownButtonFormField<String>(
          initialValue: _actorFilter,
          dropdownColor: const Color(0xFF172554),
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(labelText: 'الموظف أو المنفذ'),
          items: [
            const DropdownMenuItem(value: '', child: Text('الجميع')),
            ..._actors.map(
              (actor) => DropdownMenuItem(value: actor, child: Text(actor)),
            ),
          ],
          onChanged: (value) => setState(() => _actorFilter = value ?? ''),
        ),
        const SizedBox(height: 10),
        DropdownButtonFormField<String>(
          initialValue: _typeFilter,
          dropdownColor: const Color(0xFF172554),
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(labelText: 'نوع العملية'),
          items: const [
            DropdownMenuItem(value: '', child: Text('جميع العمليات')),
            DropdownMenuItem(value: 'security', child: Text('الأمان والدخول')),
            DropdownMenuItem(
              value: 'employee',
              child: Text('الموظفون والصلاحيات'),
            ),
            DropdownMenuItem(
              value: 'appointment',
              child: Text('المواعيد والطلبات'),
            ),
            DropdownMenuItem(
              value: 'organization',
              child: Text('بيانات المؤسسة'),
            ),
          ],
          onChanged: (value) => setState(() => _typeFilter = value ?? ''),
        ),
        const SizedBox(height: 10),
        OutlinedButton.icon(
          onPressed: _pickDate,
          icon: const Icon(Icons.calendar_month_outlined),
          label: Text(
            _dateFilter == null
                ? 'اختيار التاريخ'
                : '${_dateFilter!.day}/${_dateFilter!.month}/${_dateFilter!.year}',
          ),
        ),
        if (_actorFilter.isNotEmpty ||
            _typeFilter.isNotEmpty ||
            _dateFilter != null)
          TextButton.icon(
            onPressed: _clearFilters,
            icon: const Icon(Icons.filter_alt_off_outlined),
            label: const Text('مسح الفلاتر'),
          ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
        title: const Text('سجل العمليات'),
        actions: [
          IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _error!,
                  style: const TextStyle(color: Colors.orangeAccent),
                ),
              ),
            )
          : _logs.isEmpty
          ? const Center(
              child: Text(
                'لا توجد عمليات مسجلة بعد',
                style: TextStyle(color: Colors.white70),
              ),
            )
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView.separated(
                padding: const EdgeInsets.all(16),
                itemCount: _filteredLogs.isEmpty ? 2 : _filteredLogs.length + 1,
                separatorBuilder: (_, _) => const SizedBox(height: 10),
                itemBuilder: (_, index) {
                  if (index == 0) return _filters();
                  final filtered = _filteredLogs;
                  if (filtered.isEmpty) {
                    return const Padding(
                      padding: EdgeInsets.all(28),
                      child: Text(
                        'لا توجد عمليات مطابقة للفلاتر',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.white70),
                      ),
                    );
                  }
                  final log = filtered[index - 1];
                  final actor = _actor(log);
                  return Container(
                    padding: const EdgeInsets.all(15),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(15),
                      border: Border.all(color: Colors.white12),
                    ),
                    child: Row(
                      children: [
                        const CircleAvatar(
                          backgroundColor: Color(0xFF0C4A6E),
                          child: Icon(Icons.history, color: Color(0xFF7DD3FC)),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                (log['summary'] ?? 'عملية مسجلة').toString(),
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 6),
                              Text(
                                'بواسطة: $actor',
                                style: const TextStyle(
                                  color: Color(0xFF7DD3FC),
                                ),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                _time(log['created_at']),
                                style: const TextStyle(
                                  color: Colors.white54,
                                  fontSize: 12,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
            ),
    ),
  );
}

class TrustedDevicesPage extends StatefulWidget {
  const TrustedDevicesPage({super.key});
  @override
  State<TrustedDevicesPage> createState() => _TrustedDevicesPageState();
}

class _TrustedDevicesPageState extends State<TrustedDevicesPage> {
  List<Map<String, dynamic>> _sessions = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<KhdoomCloudApi?> _api() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return null;
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  Future<void> _load() async {
    if (mounted)
      setState(() {
        _loading = true;
        _error = null;
      });
    final api = await _api();
    if (api == null) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'سجل الدخول لعرض الأجهزة';
        });
      return;
    }
    try {
      final rows = await api.securitySessions();
      if (!mounted) return;
      setState(() {
        _sessions = rows
            .map((row) => Map<String, dynamic>.from(row as Map))
            .toList();
        _loading = false;
      });
    } on CloudApiException catch (error) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = error.message;
        });
    } on SocketException {
      if (mounted)
        setState(() {
          _loading = false;
          _error = 'تعذر الاتصال بخادم خدووم';
        });
    } finally {
      api.close();
    }
  }

  String _date(dynamic value) {
    final date = DateTime.tryParse(value?.toString() ?? '')?.toLocal();
    if (date == null) return 'غير معروف';
    final hour = date.hour % 12 == 0 ? 12 : date.hour % 12;
    final period = date.hour < 12 ? 'ص' : 'م';
    return '${date.day}/${date.month}/${date.year} â€” $hour:${date.minute.toString().padLeft(2, '0')} $period';
  }

  Future<void> _setTrusted(Map<String, dynamic> session, bool value) async {
    final api = await _api();
    if (api == null) return;
    try {
      await api.setSessionTrusted(session['id'], value);
      if (mounted) setState(() => session['trusted'] = value);
    } on CloudApiException catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      api.close();
    }
  }

  Future<void> _disconnect(Map<String, dynamic> session) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('فصل الجهاز'),
        content: Text('هل تريد تسجيل خروج ${session['device_name']}؟'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('فصل الجهاز'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final api = await _api();
    if (api == null) return;
    try {
      await api.disconnectSession(session['id']);
      await _load();
    } on CloudApiException catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      api.close();
    }
  }

  Future<void> _disconnectOthers() async {
    final confirmed = await confirmSensitiveDeletion(
      context,
      title: 'فصل جميع الأجهزة الأخرى',
      message: 'سيتم تسجيل خروج جميع الموظفين والأجهزة الأخرى، وسيبقى هذا الجهاز فقط.',
      confirmLabel: 'تأكيد الفصل',
    );
    if (confirmed != true) return;
    final api = await _api();
    if (api == null) return;
    try {
      final count = await api.logoutAllSessions();
      await _load();
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('تم فصل $count جلسات')));
    } on CloudApiException catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      api.close();
    }
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
        title: const Text('الأجهزة والجلسات'),
        actions: [
          IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _error!,
                  style: const TextStyle(color: Colors.orangeAccent),
                ),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                FilledButton.icon(
                  onPressed: _sessions.length > 1 ? _disconnectOthers : null,
                  icon: const Icon(Icons.logout),
                  label: const Text('فصل جميع الأجهزة الأخرى'),
                ),
                const SizedBox(height: 14),
                ..._sessions.map((session) {
                  final current = session['current'] == true;
                  final trusted = session['trusted'] == true;
                  return Card(
                    color: const Color(0xFF172554),
                    margin: const EdgeInsets.only(bottom: 12),
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(
                        children: [
                          ListTile(
                            contentPadding: EdgeInsets.zero,
                            leading: Icon(
                              Icons.phone_android,
                              color: current
                                  ? Colors.greenAccent
                                  : const Color(0xFF7DD3FC),
                              size: 34,
                            ),
                            title: Text(
                              session['device_name']?.toString() ??
                                  'جهاز غير معروف',
                              style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            subtitle: Text(
                              '${session['user_name'] ?? ''}${current ? ' • هذا الجهاز' : ''}\nآخر نشاط: ${_date(session['last_seen_at'])}',
                              style: const TextStyle(color: Colors.white60),
                            ),
                            isThreeLine: true,
                          ),
                          SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            value: trusted,
                            onChanged: (value) => _setTrusted(session, value),
                            title: const Text(
                              'جهاز موثوق',
                              style: TextStyle(color: Colors.white),
                            ),
                          ),
                          if (!current)
                            OutlinedButton.icon(
                              onPressed: () => _disconnect(session),
                              icon: const Icon(Icons.link_off),
                              label: const Text('فصل الجهاز'),
                            ),
                        ],
                      ),
                    ),
                  );
                }),
              ],
            ),
    ),
  );
}

class DashboardCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;

  const DashboardCard({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xFF111B35),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: const Color(0xFF38BDF8).withValues(alpha: 0.25),
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: LayoutBuilder(
            builder: (context, constraints) => FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.center,
              child: SizedBox(
                width: constraints.maxWidth,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(icon, color: const Color(0xFF38BDF8), size: 28),
                    const SizedBox(height: 4),
                    Text(
                      title,
                      textDirection: TextDirection.rtl,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      subtitle,
                      textDirection: TextDirection.rtl,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white60,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class MyBusinessPage extends StatefulWidget {
  const MyBusinessPage({super.key});

  @override
  State<MyBusinessPage> createState() => _MyBusinessPageState();
}

class _MyBusinessPageState extends State<MyBusinessPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  String businessName = 'مؤسستي';
  String activity = 'غير محدد';
  String phone = 'غير مضاف';
  String? businessLogoPath;
  bool _isAdmin = false;

  @override
  void initState() {
    super.initState();
    loadBusinessData();
  }

  Future<void> loadBusinessData() async {
    final prefs = await _branchPrefs;

    final savedBusinessName =
        prefs.getString('businessName') ??
        prefs.getString('account_business_name') ??
        'مؤسستي';
    final savedPhone =
        prefs.getString('phone') ??
        prefs.getString('account_phone') ??
        'غير مضاف';

    if (!mounted) return;
    setState(() {
      businessName = savedBusinessName;
      activity = prefs.getString('activity') ?? 'غير محدد';
      phone = savedPhone;
      businessLogoPath = prefs.getString('business_logo_path');
      _isAdmin = prefs.getString('session_user_type') != 'employee';
    });
  }

  Future<void> _pickBusinessLogo() async {
    if (!_isAdmin) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تغيير شعار المؤسسة متاح للمالك فقط')),
      );
      return;
    }
    try {
      const channel = MethodChannel('khdoom/profile_image');
      final destinationPath = await channel.invokeMethod<String>('pickImage', {
        'fileName':
            'khdoom_business_logo_${DateTime.now().microsecondsSinceEpoch}',
      });
      if (destinationPath == null || destinationPath.isEmpty) return;
      final prefs = await _branchPrefs;
      await prefs.setString('business_logo_path', destinationPath);
      if (!mounted) return;
      setState(() => businessLogoPath = destinationPath);
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('تم حفظ شعار المؤسسة')));
    } on PlatformException catch (_) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تعذر اختيار الشعار، حاول مرة أخرى')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [PageRefreshButton(onRefresh: loadBusinessData)],
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          elevation: 0,
          centerTitle: true,
          title: const Text(
            'مؤسستي',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: const Color(0xFF111B35),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                    color: const Color(0xFF38BDF8).withValues(alpha: 0.25),
                  ),
                ),
                child: Row(
                  children: [
                    InkWell(
                      onTap:
                          businessLogoPath != null &&
                              businessLogoPath!.isNotEmpty
                          ? () => Navigator.push<void>(
                              context,
                              MaterialPageRoute(
                                builder: (_) => DocumentImagePage(
                                  path: businessLogoPath!,
                                  title: 'شعار المؤسسة',
                                ),
                              ),
                            )
                          : (_isAdmin ? _pickBusinessLogo : null),
                      borderRadius: BorderRadius.circular(40),
                      child: CircleAvatar(
                        radius: 34,
                        backgroundColor: const Color(0xFF172554),
                        backgroundImage:
                            businessLogoPath != null &&
                                File(businessLogoPath!).existsSync()
                            ? FileImage(File(businessLogoPath!))
                            : null,
                        child:
                            businessLogoPath == null ||
                                !File(businessLogoPath!).existsSync()
                            ? const Icon(
                                Icons.add_photo_alternate_outlined,
                                color: Color(0xFF38BDF8),
                                size: 32,
                              )
                            : null,
                      ),
                    ),
                    SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'اسم المؤسسة',
                            style: TextStyle(
                              color: Colors.white60,
                              fontSize: 13,
                            ),
                          ),
                          SizedBox(height: 5),
                          Text(
                            businessName,
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 20,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              if (_isAdmin)
                TextButton.icon(
                  onPressed: _pickBusinessLogo,
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                  label: Text(
                    businessLogoPath == null
                        ? 'إضافة شعار المؤسسة'
                        : 'تغيير شعار المؤسسة',
                  ),
                )
              else
                const Padding(
                  padding: EdgeInsets.only(top: 10),
                  child: Text(
                    'شعار المؤسسة — للمشاهدة فقط، والتغيير متاح للمالك.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.white60, fontSize: 12),
                  ),
                ),

              const SizedBox(height: 12),

              BusinessInfoTile(
                icon: Icons.work_outline,
                title: 'النشاط',
                value: activity,
              ),

              const SizedBox(height: 12),

              BusinessInfoTile(
                icon: Icons.phone_outlined,
                title: 'رقم التواصل',
                value: phone,
              ),

              const SizedBox(height: 12),

              BusinessInfoTile(
                icon: Icons.workspace_premium_outlined,
                title: 'حالة الاشتراك',
                value: 'تجريبي',
              ),

              const SizedBox(height: 12),

              const BusinessInfoTile(
                icon: Icons.data_usage,
                title: 'الوحدات المتبقية',
                value: '250 وحدة',
              ),

              const SizedBox(height: 25),

              ElevatedButton.icon(
                onPressed: () async {
                  final result = await Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (context) => EditBusinessPage(
                        currentName: businessName,
                        currentActivity: activity,
                        currentPhone: phone,
                      ),
                    ),
                  );

                  if (result != null) {
                    setState(() {
                      businessName = result['name'];
                      activity = result['activity'];
                      phone = result['phone'];
                    });

                    final prefs = await _branchPrefs;

                    await prefs.setString('businessName', businessName);
                    await prefs.setString('activity', activity);
                    await prefs.setString('phone', phone);
                    if (prefs.branchId == BranchPreferences.mainId) {
                      await prefs.setString(
                        'account_business_name',
                        businessName,
                      );
                      await prefs.setString('account_phone', phone);
                    }
                  }
                },
                icon: const Icon(Icons.edit),
                label: const Text('تعديل بيانات المؤسسة'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF38BDF8),
                  foregroundColor: const Color(0xFF0B1020),
                  minimumSize: const Size.fromHeight(52),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(16),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class BusinessInfoTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String value;

  const BusinessInfoTile({
    super.key,
    required this.icon,
    required this.title,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(17),
      decoration: BoxDecoration(
        color: const Color(0xFF111B35),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(
          color: const Color(0xFF38BDF8).withValues(alpha: 0.20),
        ),
      ),
      child: Row(
        children: [
          Icon(icon, color: const Color(0xFF38BDF8), size: 27),
          const SizedBox(width: 15),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(color: Colors.white60, fontSize: 13),
                ),
                const SizedBox(height: 4),
                Text(
                  value,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class EditBusinessPage extends StatefulWidget {
  final String currentName;
  final String currentActivity;
  final String currentPhone;

  const EditBusinessPage({
    super.key,
    required this.currentName,
    required this.currentActivity,
    required this.currentPhone,
  });

  @override
  State<EditBusinessPage> createState() => _EditBusinessPageState();
}

class _EditBusinessPageState extends State<EditBusinessPage> {
  late TextEditingController nameController;
  late TextEditingController activityController;
  late TextEditingController phoneController;

  @override
  void initState() {
    super.initState();

    nameController = TextEditingController(text: widget.currentName);

    activityController = TextEditingController(text: widget.currentActivity);

    phoneController = TextEditingController(text: widget.currentPhone);
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('تعديل بيانات المؤسسة'),
          centerTitle: true,
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Text('اسم المؤسسة', style: TextStyle(color: Colors.white70)),
            const SizedBox(height: 8),
            TextField(
              controller: nameController,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                filled: true,
                fillColor: const Color(0xFF111B35),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(15),
                ),
              ),
            ),
            const SizedBox(height: 18),

            const Text('النشاط', style: TextStyle(color: Colors.white70)),
            const SizedBox(height: 8),
            TextField(
              controller: activityController,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                hintText: 'مثال: زجاج ومرايا',
                hintStyle: const TextStyle(color: Colors.white38),
                filled: true,
                fillColor: const Color(0xFF111B35),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(15),
                ),
              ),
            ),
            const SizedBox(height: 18),

            const Text('رقم التواصل', style: TextStyle(color: Colors.white70)),
            const SizedBox(height: 8),
            TextField(
              controller: phoneController,
              keyboardType: TextInputType.phone,
              style: const TextStyle(color: Colors.white),
              decoration: InputDecoration(
                hintText: '05xxxxxxxx',
                hintStyle: const TextStyle(color: Colors.white38),
                filled: true,
                fillColor: const Color(0xFF111B35),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(15),
                ),
              ),
            ),
            const SizedBox(height: 30),

            ElevatedButton(
              onPressed: () {
                Navigator.pop(context, {
                  'name': nameController.text.trim(),
                  'activity': activityController.text.trim(),
                  'phone': phoneController.text.trim(),
                });
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF38BDF8),
                foregroundColor: const Color(0xFF0B1020),
                minimumSize: const Size.fromHeight(52),
              ),
              child: const Text(
                'حفظ التعديلات',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class AppointmentsRequestsPage extends StatefulWidget {
  const AppointmentsRequestsPage({super.key});

  @override
  State<AppointmentsRequestsPage> createState() =>
      _AppointmentsRequestsPageState();
}

class _AppointmentsRequestsPageState extends State<AppointmentsRequestsPage> {
  late final Future<BranchPreferences> _branchPrefs;
  BranchPreferences? _scope;
  String? _loadError;
  bool _copyingChatLink = false;

  Future<void> _copyBranchChatLink() async {
    if (_copyingChatLink) return;
    setState(() => _copyingChatLink = true);
    KhdoomCloudApi? api;
    try {
      final prefs = await _branchPrefs;
      const storage = FlutterSecureStorage();
      final token = await storage.read(key: 'cloud_session_token');
      if (token == null || token.isEmpty) {
        throw const CloudApiException(401, 'سجّل الدخول بالحساب السحابي أولًا');
      }
      api = KhdoomCloudApi(
        scope: prefs,
        baseUrl:
            prefs.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      )..token = token;
      final link = await api.branchChatLink();
      await Clipboard.setData(ClipboardData(text: link));
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'تم نسخ رابط شات ${prefs.branchName}؛ طلباته تصل لهذا الفرع فقط',
            ),
          ),
        );
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              error is CloudApiException
                  ? error.message
                  : 'تعذر نسخ رابط الشات؛ حاول مرة أخرى',
            ),
          ),
        );
    } finally {
      api?.close();
      if (mounted) setState(() => _copyingChatLink = false);
    }
  }

  static const _storageKey = 'business_appointments_requests';
  Timer? _refreshTimer;
  bool _fetching = false;
  final List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _canManage = true;

  @override
  void initState() {
    super.initState();
    _branchPrefs = BranchPreferences.getInstance();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _load();
    });
    _refreshTimer = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    if (_fetching || !mounted || ModalRoute.of(context)?.isCurrent == false)
      return;
    _fetching = true;
    try {
      await _loadRecords();
    } finally {
      _fetching = false;
    }
  }

  Future<void> _loadRecords() async {
    final prefs = await _branchPrefs;
    _scope = prefs;
    _loadError = null;
    if (prefs.getString('session_user_type') == 'employee') {
      _canManage = false;
      final employeeId = prefs.getString('session_employee_id');
      final employeesJson = prefs.getString('business_employees');
      if (employeeId != null && employeesJson != null) {
        for (final raw in jsonDecode(employeesJson) as List) {
          final employee = Map<String, dynamic>.from(raw as Map);
          if (employee['id'].toString() == employeeId) {
            final employeePermissions = Map<String, dynamic>.from(
              employee['permissions'] as Map? ?? {},
            );
            _canManage = employeePermissions['manageAppointments'] == true;
            break;
          }
        }
      }
    }
    final saved = prefs.getString(_storageKey);
    _items.clear();
    if (saved != null) {
      try {
        _items.addAll(
          (jsonDecode(saved) as List).map(
            (item) => Map<String, dynamic>.from(item as Map),
          ),
        );
      } catch (_) {
        // تبدأ القائمة فارغة إذا كانت البيانات المحلية غير صالحة.
      }
    }
    const storage = FlutterSecureStorage();
    final cloudToken = await storage.read(key: 'cloud_session_token');
    if (cloudToken != null && cloudToken.isNotEmpty) {
      final api = KhdoomCloudApi(
        scope: prefs,
        baseUrl:
            prefs.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      )..token = cloudToken;
      try {
        final cloudItems = await api.appointments();
        _items.removeWhere((item) => item['cloudId'] != null);
        for (final raw in cloudItems) {
          final item = Map<String, dynamic>.from(raw as Map);
          final scheduled = DateTime.tryParse(
            item['scheduled_at']?.toString() ?? '',
          );
          _items.add({
            'id': 'cloud_${item['id']}',
            'cloudId': item['id'],
            'source': item['source'] ?? 'public_chat',
            'followups': item['followups'] ?? [],
            'followup_count': item['followup_count'] ?? 0,
            'followup_latest_id': item['followup_latest_id'] ?? 0,
            'type': item['request_type'] ?? 'طلب عميل',
            'title': item['title'] ?? 'طلب عميل',
            'customer': item['customer_name'] ?? '',
            'phone': item['phone'] ?? '',
            'notes': item['notes'] ?? '',
            'date': scheduled?.toLocal().toIso8601String() ?? '',
            'reminderChoice': 'none',
            'reminderDate': null,
            'status': item['status'] ?? 'pending',
            'createdAt': item['created_at'] ?? DateTime.now().toIso8601String(),
          });
        }
        await _save();
      } catch (_) {
        _loadError =
            'تعذر تحديث طلبات الشات؛ المعروض هو المحفوظ سابقًا. أعد التحديث.';
      } finally {
        api.close();
      }
    } else {
      _loadError = 'سجّل الدخول بالحساب السحابي لعرض طلبات الشات.';
    }
    _sortItems();
    if (mounted) setState(() => _loading = false);
  }

  void _sortItems() {
    _items.sort((a, b) {
      final aFollowup = (a['followup_count'] as num? ?? 0) > 0;
      final bFollowup = (b['followup_count'] as num? ?? 0) > 0;
      if (aFollowup != bFollowup) return aFollowup ? -1 : 1;
      final firstPending = a['status'] == 'pending';
      final secondPending = b['status'] == 'pending';
      if (firstPending != secondPending) return firstPending ? -1 : 1;
      final first = DateTime.tryParse(a['createdAt']?.toString() ?? '');
      final second = DateTime.tryParse(b['createdAt']?.toString() ?? '');
      if (first == null && second == null) return 0;
      if (first == null) return 1;
      if (second == null) return -1;
      return second.compareTo(first);
    });
  }

  Future<void> _save() async {
    final prefs = await _branchPrefs;
    await prefs.setString(_storageKey, jsonEncode(_items));
  }

  String _formatDate(dynamic raw) {
    final date = DateTime.tryParse(raw?.toString() ?? '');
    if (date == null) return 'موعد غير محدد';
    final day = date.day.toString().padLeft(2, '0');
    final month = date.month.toString().padLeft(2, '0');
    final hour12 = date.hour % 12 == 0 ? 12 : date.hour % 12;
    final minute = date.minute.toString().padLeft(2, '0');
    final period = date.hour < 12 ? 'ص' : 'م';
    return '$day/$month/${date.year} â€” $hour12:$minute $period';
  }

  Future<bool> _appointmentsAvailable() async {
    final prefs = await _branchPrefs;
    final token = await const FlutterSecureStorage().read(
      key: 'cloud_session_token',
    );
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      if (token == null || token.isEmpty)
        throw Exception('سجل الدخول للتحقق من حالة المواعيد');
      final status = await api.maintenanceStatus();
      final appointments = status['appointments'];
      if (appointments is! Map)
        throw Exception('يلزم تحديث الخادم للتحقق من حالة المواعيد');
      if (appointments['active'] == true)
        throw Exception(appointments['message'] ?? 'المواعيد متوقفة مؤقتًا');
      return true;
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('$error')));
      return false;
    } finally {
      api.close();
    }
  }

  Future<void> _showAddForm({int? index}) async {
    if (!await _appointmentsAvailable() || !mounted) return;
    if (_fetching) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('جارٍ تحديث المواعيد، حاول بعد لحظة')),
      );
      return;
    }
    final current = index == null ? null : _items[index];
    final titleController = TextEditingController(
      text: current?['title']?.toString() ?? '',
    );
    final customerController = TextEditingController(
      text: current?['customer']?.toString() ?? '',
    );
    final phoneController = TextEditingController(
      text: current?['phone']?.toString() ?? '',
    );
    final notesController = TextEditingController(
      text: current?['notes']?.toString() ?? '',
    );
    var type = current?['type']?.toString() ?? 'موعد مقاس';
    if (type == 'موعد') type = 'موعد مقاس';
    if (type == 'طلب') type = 'طلب عميل';
    var selectedDate =
        DateTime.tryParse(current?['date']?.toString() ?? '') ??
        DateTime.now().add(const Duration(days: 1));
    var reminderChoice = current?['reminderChoice']?.toString() ?? '60';
    if (reminderChoice == 'custom') reminderChoice = '1';

    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text(
            index == null ? 'إضافة موعد أو طلب' : 'تعديل الموعد أو الطلب',
          ),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: type,
                  decoration: const InputDecoration(labelText: 'النوع'),
                  items: const [
                    DropdownMenuItem(
                      value: 'موعد مقاس',
                      child: Text('موعد مقاس'),
                    ),
                    DropdownMenuItem(
                      value: 'موعد صيانة',
                      child: Text('موعد صيانة'),
                    ),
                    DropdownMenuItem(
                      value: 'طلب عميل',
                      child: Text('طلب عميل'),
                    ),
                  ],
                  onChanged: (value) {
                    if (value != null) setDialogState(() => type = value);
                  },
                ),
                TextField(
                  controller: titleController,
                  decoration: const InputDecoration(
                    labelText: 'عنوان الموعد أو الطلب',
                  ),
                ),
                TextField(
                  controller: customerController,
                  decoration: const InputDecoration(labelText: 'اسم العميل'),
                ),
                TextField(
                  controller: phoneController,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(labelText: 'رقم التواصل'),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: () async {
                    final date = await showDatePicker(
                      context: context,
                      initialDate: selectedDate,
                      firstDate: DateTime.now().subtract(
                        const Duration(days: 365),
                      ),
                      lastDate: DateTime.now().add(const Duration(days: 3650)),
                    );
                    if (date == null || !context.mounted) return;
                    final time = await showTimePicker(
                      context: context,
                      initialTime: TimeOfDay.fromDateTime(selectedDate),
                    );
                    if (time == null) return;
                    setDialogState(() {
                      selectedDate = DateTime(
                        date.year,
                        date.month,
                        date.day,
                        time.hour,
                        time.minute,
                      );
                    });
                  },
                  icon: const Icon(Icons.event),
                  label: Text(
                    'تاريخ ووقت الموعد: ' +
                        _formatDate(selectedDate.toIso8601String()),
                  ),
                ),
                const SizedBox(height: 4),
                DropdownButtonFormField<String>(
                  initialValue: reminderChoice,
                  decoration: const InputDecoration(
                    labelText: 'التنبيه قبل الموعد',
                  ),
                  items: const [
                    DropdownMenuItem(value: 'none', child: Text('بدون تنبيه')),
                    DropdownMenuItem(value: '1', child: Text('قبل دقيقة')),
                    DropdownMenuItem(value: '15', child: Text('قبل 15 دقيقة')),
                    DropdownMenuItem(value: '30', child: Text('قبل 30 دقيقة')),
                    DropdownMenuItem(value: '60', child: Text('قبل ساعة')),
                    DropdownMenuItem(value: '1440', child: Text('قبل يوم')),
                  ],
                  onChanged: (value) {
                    if (value != null) {
                      setDialogState(() => reminderChoice = value);
                    }
                  },
                ),
                TextField(
                  controller: notesController,
                  maxLines: 3,
                  decoration: const InputDecoration(labelText: 'ملاحظات'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: () {
                final title = titleController.text.trim();
                if (title.isEmpty) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('اكتب عنوان الموعد أو الطلب')),
                  );
                  return;
                }
                DateTime? reminderDate;
                if (reminderChoice != 'none') {
                  final minutes = int.tryParse(reminderChoice) ?? 60;
                  reminderDate = selectedDate.subtract(
                    Duration(minutes: minutes),
                  );
                }
                if (reminderDate != null &&
                    !reminderDate.isAfter(DateTime.now())) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('وقت التنبيه يجب أن يكون في المستقبل'),
                    ),
                  );
                  return;
                }
                if (reminderDate != null &&
                    !reminderDate.isBefore(selectedDate)) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('وقت التنبيه يجب أن يكون قبل الموعد'),
                    ),
                  );
                  return;
                }
                Navigator.pop(dialogContext, {
                  'id':
                      current?['id'] ??
                      DateTime.now().microsecondsSinceEpoch.toString(),
                  'type': type,
                  'title': title,
                  'customer': customerController.text.trim(),
                  'phone': phoneController.text.trim(),
                  'notes': notesController.text.trim(),
                  'date': selectedDate.toIso8601String(),
                  'reminderChoice': reminderChoice,
                  'reminderDate': reminderDate?.toIso8601String(),
                  'status': current?['status'] ?? 'pending',
                  'createdAt':
                      current?['createdAt'] ?? DateTime.now().toIso8601String(),
                });
              },
              child: const Text('حفظ'),
            ),
          ],
        ),
      ),
    );

    if (result == null || !mounted) return;
    if (!await _appointmentsAvailable() || !mounted) return;
    if (current?['cloudId'] != null) {
      try {
        await _updateCloudAppointment(current!, result, 'accepted');
        result['cloudId'] = current['cloudId'];
        result['source'] = current['source'];
        result['status'] = 'accepted';
      } on CloudApiException catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
        return;
      } on SocketException {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر الاتصال بخادم خدووم')),
        );
        return;
      }
    }
    setState(() {
      if (index == null) {
        _items.add(result);
      } else {
        _items[index] = result;
      }
      _sortItems();
    });
    await _save();
    await KhdoomNotifications.cancelAppointment(
      result['id'],
      scope: await _branchPrefs,
    );
    await KhdoomNotifications.scheduleAppointment(
      result,
      scope: await _branchPrefs,
    );
  }

  Future<void> _syncCloudStatus(
    Map<String, dynamic> item,
    String status,
  ) async {
    final cloudId = item['cloudId'];
    if (cloudId == null) return;
    final prefs = await _branchPrefs;
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) {
      throw const CloudApiException(401, 'سجل الدخول لمزامنة الطلب');
    }
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      await api.updateAppointmentStatus(cloudId, status);
    } finally {
      api.close();
    }
  }

  Future<void> _updateCloudAppointment(
    Map<String, dynamic> item,
    Map<String, dynamic> values,
    String status, {
    String? replyMessage,
  }) async {
    final cloudId = item['cloudId'];
    if (cloudId == null) return;
    final prefs = await _branchPrefs;
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) {
      throw const CloudApiException(401, 'سجل الدخول لمزامنة الطلب');
    }
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      await api.updateAppointment(cloudId, {
        'status': status,
        'type': values['type'],
        'title': values['title'],
        'customer': values['customer'],
        'phone': values['phone'],
        'notes': values['notes'],
        'scheduledAt': DateTime.parse(values['date'].toString())
            .toUtc()
            .toIso8601String(),
        if ((item['followup_latest_id'] as num? ?? 0) > 0)
          'followupThrough': item['followup_latest_id'],
        if (replyMessage != null && replyMessage.trim().isNotEmpty)
          'replyMessage': replyMessage.trim(),
      });
    } finally {
      api.close();
    }
  }

  Future<void> _rejectRequest(int index) async {
    final reasonController = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('رفض طلب الموعد'),
        content: TextField(
          controller: reasonController,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'سبب الرفض أو الرسالة للزبون',
            hintText: 'مثال: الوقت غير متاح، اختر موعدًا آخر',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(dialogContext, reasonController.text.trim()),
            child: const Text('رفض وإرسال'),
          ),
        ],
      ),
    );
    if (reason == null || !mounted) return;
    final item = _items[index];
    final message = reason.isEmpty
        ? 'نعتذر، تعذر قبول الموعد المطلوب. يرجى اختيار موعد آخر.'
        : 'نعتذر، تعذر قبول الموعد: $reason';
    try {
      await _updateCloudAppointment(
        item,
        item,
        'rejected',
        replyMessage: message,
      );
      if (!mounted) return;
      setState(() => item['status'] = 'rejected');
      await _save();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تم رفض الطلب وإبلاغ الزبون')),
      );
    } on CloudApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    } on SocketException {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('تعذر الاتصال بخادم خدووم')));
    }
  }

  Future<void> _acceptRequest(int index) async {
    final item = _items[index];
    if (!await _appointmentsAvailable() || !mounted) return;
    try {
      await _syncCloudStatus(item, 'accepted');
      if (!mounted) return;
      setState(() => item['status'] = 'accepted');
      await _save();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تم قبول الموعد وإبلاغ الخادم ✓')),
      );
    } on CloudApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    } on SocketException {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('تعذر الاتصال بخادم خدووم')));
    }
  }

  Future<void> _toggleComplete(int index) async {
    final item = _items[index];
    final previousStatus = item['status']?.toString() ?? 'pending';
    final nextStatus = previousStatus == 'completed' ? 'accepted' : 'completed';
    if (!await _appointmentsAvailable() || !mounted) return;
    try {
      await _syncCloudStatus(item, nextStatus);
    } on CloudApiException catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
      return;
    } on SocketException {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('تعذر الاتصال بخادم خدووم')));
      return;
    }
    if (!mounted) return;
    setState(() => item['status'] = nextStatus);
    await _save();
    await KhdoomNotifications.cancelAppointment(
      item['id'],
      scope: await _branchPrefs,
    );
    if (item['status'] != 'completed') {
      await KhdoomNotifications.scheduleAppointment(
        item,
        scope: await _branchPrefs,
      );
    }
  }

  Future<void> _delete(int index) async {
    if (!_canManage || _fetching) return;
    final target = Map<String, dynamic>.from(_items[index]);
    final confirmationController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('حذف السجل'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'سيُزال الطلب من قائمة المواعيد. لطلبات الشات تُحفظ المحادثة وسجل الطلب في الخادم.',
              ),
              const SizedBox(height: 12),
              const Text('اكتب كلمة «تأكيد» لإكمال الحذف.'),
              TextField(
                controller: confirmationController,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'تأكيد العملية'),
                onChanged: (_) => setDialogState(() {}),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed:
                  {
                    'تأكيد',
                    'تاكيد',
                  }.contains(confirmationController.text.trim())
                  ? () => Navigator.pop(dialogContext, true)
                  : null,
              child: const Text('حذف'),
            ),
          ],
        ),
      ),
    );
    await Future<void>.delayed(const Duration(milliseconds: 400));
    confirmationController.dispose();
    if (confirmed != true || !mounted) return;
    final removed = target;
    if (removed['cloudId'] != null) {
      final prefs = await _branchPrefs;
      final token = await const FlutterSecureStorage().read(
        key: 'cloud_session_token',
      );
      if (token == null || token.isEmpty) {
        if (mounted)
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('سجل الدخول لحذف الطلب من الخادم')),
          );
        return;
      }
      final api = KhdoomCloudApi(
        scope: prefs,
        baseUrl:
            prefs.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      )..token = token;
      try {
        await api.deleteAppointment(removed['cloudId']);
      } catch (error) {
        if (mounted)
          ScaffoldMessenger.of(context)
              .showSnackBar(SnackBar(content: Text('لم يُحذف الطلب: $error')));
        return;
      } finally {
        api.close();
      }
    }
    if (!mounted) return;
    setState(() => _items.removeWhere((item) => item['id'] == removed['id']));
    await _save();
    await KhdoomNotifications.cancelAppointment(
      removed['id'],
      scope: await _branchPrefs,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [PageRefreshButton(onRefresh: _load)],
          title: const Text('المواعيد والطلبات'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          bottom: PreferredSize(
            preferredSize: Size.fromHeight(
              (_scope?.getString('session_user_type') == 'admin' ? 100 : 44) +
                  (_loadError == null ? 0 : 60),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Column(
                children: [
                  Text(
                    'الفرع الحالي: ${_scope?.branchName ?? 'جارٍ التحميل'}',
                    style: const TextStyle(color: Colors.white70),
                  ),
                  if (_scope != null &&
                      _scope!.getString('session_user_type') == 'admin')
                    TextButton.icon(
                      icon: const Icon(Icons.forum_outlined),
                      label: const Text('نسخ رابط شات هذا الفرع'),
                      onPressed: _copyingChatLink ? null : _copyBranchChatLink,
                    ),
                  if (_loadError != null)
                    Text(
                      _loadError!,
                      style: const TextStyle(color: Color(0xFFFBBF24)),
                    ),
                ],
              ),
            ),
          ),
        ),
        floatingActionButton: _canManage
            ? FloatingActionButton.extended(
                onPressed: () => _showAddForm(),
                icon: const Icon(Icons.add),
                label: const Text('إضافة'),
              )
            : null,
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _items.isEmpty
            ? const Center(
                child: Text(
                  'لا توجد مواعيد أو طلبات حتى الآن',
                  style: TextStyle(color: Colors.white70, fontSize: 17),
                ),
              )
            : ListView.separated(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 90),
                itemCount: _items.length,
                separatorBuilder: (_, _) => const SizedBox(height: 12),
                itemBuilder: (context, index) {
                  final item = _items[index];
                  final completed = item['status'] == 'completed';
                  final pendingFromChat =
                      item['cloudId'] != null && item['status'] == 'pending';
                  final customer = item['customer']?.toString() ?? '';
                  final phone = item['phone']?.toString() ?? '';
                  final notes = item['notes']?.toString() ?? '';
                  final reminderDate = DateTime.tryParse(
                    item['reminderDate']?.toString() ?? '',
                  );
                  return Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(
                        color: completed
                            ? const Color(0xFF22C55E)
                            : const Color(0xFF38BDF8).withValues(alpha: 0.35),
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(
                              item['type'] == 'طلب عميل'
                                  ? Icons.inbox_outlined
                                  : Icons.event_available,
                              color: completed
                                  ? const Color(0xFF22C55E)
                                  : const Color(0xFF38BDF8),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                item['title']?.toString() ?? '',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 16,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                            if (_canManage && item['cloudId'] != null)
                              IconButton(
                                tooltip: 'حذف من القائمة',
                                onPressed: () => _delete(index),
                                icon: const Icon(
                                  Icons.delete_outline,
                                  color: Color(0xFFEF4444),
                                ),
                              ),
                            if (_canManage && item['cloudId'] == null) ...[
                              IconButton(
                                onPressed: () => _showAddForm(index: index),
                                icon: const Icon(
                                  Icons.edit_outlined,
                                  color: Color(0xFF7DD3FC),
                                ),
                              ),
                              IconButton(
                                onPressed: () => _delete(index),
                                icon: const Icon(
                                  Icons.delete_outline,
                                  color: Color(0xFFEF4444),
                                ),
                              ),
                            ] else if (_canManage &&
                                item['cloudId'] != null &&
                                item['source'] != 'human_handoff') ...[
                              IconButton(
                                tooltip: 'تعديل الموعد وإبلاغ الزبون',
                                onPressed: () => _showAddForm(index: index),
                                icon: const Icon(
                                  Icons.edit_calendar_outlined,
                                  color: Color(0xFF7DD3FC),
                                ),
                              ),
                            ],
                          ],
                        ),
                        Text(
                          item['source'] == 'human_handoff'
                              ? 'طلب تواصل — بدون موعد محجوز'
                              : _formatDate(item['date']),
                          style: const TextStyle(color: Color(0xFF7DD3FC)),
                        ),
                        AppointmentFollowupCard(
                          key: ValueKey('followup_${item['id']}'),
                          item: item,
                          canReply: _canManage,
                          onReply: (message, throughId) async {
                            final prefs = await _branchPrefs;
                            const storage = FlutterSecureStorage();
                            final token = await storage.read(
                              key: 'cloud_session_token',
                            );
                            if (token == null || token.isEmpty)
                              throw const CloudApiException(
                                401,
                                'سجّل الدخول أولًا',
                              );
                            final api = KhdoomCloudApi(
                              scope: prefs,
                              baseUrl:
                                  prefs.getString('cloud_api_url') ??
                                  'https://khdoom-api.onrender.com',
                            )..token = token;
                            try {
                              await api.replyToAppointment(
                                item['cloudId'],
                                message,
                                throughId,
                              );
                              await _load();
                            } finally {
                              api.close();
                            }
                          },
                        ),
                        if (pendingFromChat &&
                            item['source'] != 'human_handoff')
                          const Padding(
                            padding: EdgeInsets.only(top: 6),
                            child: Text(
                              'طلب جديد من شات العملاء — بانتظار الموافقة',
                              style: TextStyle(
                                color: Color(0xFFFBBF24),
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        const SizedBox(height: 3),
                        Row(
                          children: [
                            Icon(
                              reminderDate == null
                                  ? Icons.notifications_off_outlined
                                  : Icons.notifications_active_outlined,
                              color: Colors.white60,
                              size: 18,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              reminderDate == null
                                  ? 'بدون تنبيه'
                                  : 'التنبيه: ' +
                                        _formatDate(item['reminderDate']),
                              style: const TextStyle(color: Colors.white60),
                            ),
                          ],
                        ),
                        if (customer.isNotEmpty) ...[
                          const SizedBox(height: 6),
                          Text(
                            'العميل: $customer',
                            style: const TextStyle(color: Colors.white70),
                          ),
                        ],
                        if (phone.isNotEmpty)
                          Text(
                            'التواصل: $phone',
                            style: const TextStyle(color: Colors.white70),
                          ),
                        if (notes.isNotEmpty) ...[
                          const SizedBox(height: 6),
                          Text(
                            notes,
                            style: const TextStyle(color: Colors.white60),
                          ),
                        ],
                        const SizedBox(height: 10),
                        SizedBox(
                          width: double.infinity,
                          child: item['source'] == 'human_handoff'
                              ? Text(
                                  completed ? 'تم الرد على طلب التواصل' : 'استخدم «الرد على العميل» أعلاه؛ لا يحتاج الطلب حجز موعد.',
                                  style: const TextStyle(color: Colors.white70),
                                )
                              : pendingFromChat
                              ? Row(
                                  children: [
                                    Expanded(
                                      child: FilledButton.icon(
                                        onPressed: _canManage
                                            ? () => _acceptRequest(index)
                                            : null,
                                        icon: const Icon(Icons.event_available),
                                        label: const Text('قبول'),
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Expanded(
                                      child: OutlinedButton.icon(
                                        onPressed: _canManage
                                            ? () => _rejectRequest(index)
                                            : null,
                                        icon: const Icon(Icons.close),
                                        label: const Text('رفض'),
                                      ),
                                    ),
                                  ],
                                )
                              : OutlinedButton.icon(
                                  onPressed: _canManage
                                      ? () => _toggleComplete(index)
                                      : null,
                                  icon: Icon(
                                    completed ? Icons.undo : Icons.check_circle,
                                  ),
                                  label: Text(
                                    completed
                                        ? 'إعادة إلى قيد المتابعة'
                                        : 'تم الإنجاز',
                                  ),
                                ),
                        ),
                      ],
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class AlertsPage extends StatelessWidget {
  const AlertsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: const Text('التنبيهات'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            AlertCard(
              icon: Icons.event_available,
              title: 'المواعيد وطلبات العملاء',
              subtitle: 'الطلبات الجديدة والمواعيد والتنبيهات المخصصة',
              onTap: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const AppointmentsRequestsPage(),
                  ),
                );
              },
            ),
            AlertCard(
              icon: Icons.badge,
              title: 'تنبيهات الموظفين',
              subtitle: 'الإقامات والعقود والتأمين',
              onTap: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const EmployeeAlertsPage()),
                );
              },
            ),
            AlertCard(
              icon: Icons.business,
              title: 'تنبيهات المؤسسة',
              subtitle: 'السجل والرخصة وقوى ومدد',
              onTap: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const OrganizationAlertsPage(),
                  ),
                );
              },
            ),
            AlertCard(
              icon: Icons.directions_car,
              title: 'تنبيهات المركبات',
              subtitle: 'الاستمارة والفحص والتأمين',
              onTap: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const VehiclesPage()),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class EmployeeAlertsPage extends StatefulWidget {
  const EmployeeAlertsPage({super.key});

  @override
  State<EmployeeAlertsPage> createState() => _EmployeeAlertsPageState();
}

class _EmployeeAlertsPageState extends State<EmployeeAlertsPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  static const _storageKey = 'employee_alert_records';
  final List<Map<String, dynamic>> _records = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await _branchPrefs;
    final saved = prefs.getString(_storageKey);
    _records.clear();
    if (saved != null) {
      _records.addAll(
        (jsonDecode(saved) as List).map(
          (item) => Map<String, dynamic>.from(item as Map),
        ),
      );
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _save() async {
    final prefs = await _branchPrefs;
    await prefs.setString(_storageKey, jsonEncode(_records));
    await KhdoomNotifications.syncStoredAlerts();
  }

  String _format(DateTime? date) => date == null
      ? 'اختر التاريخ'
      : '${date.year}/${date.month.toString().padLeft(2, '0')}/${date.day.toString().padLeft(2, '0')}';

  Color _dateColor(String value) {
    final date = DateTime.tryParse(value);
    if (date == null) return Colors.white54;
    final days = date.difference(DateTime.now()).inDays;
    if (days < 0) return const Color(0xFFEF4444);
    if (days <= 30) return const Color(0xFFF59E0B);
    return const Color(0xFF22C55E);
  }

  String _remaining(String value) {
    final date = DateTime.tryParse(value);
    if (date == null) return 'التاريخ غير مضاف';
    final days = date.difference(DateTime.now()).inDays;
    if (days < 0) return 'منتهي منذ ${days.abs()} يوم';
    if (days == 0) return 'ينتهي اليوم';
    return 'متبقي $days يوم';
  }

  Future<void> _showForm({int? index}) async {
    final existing = index == null ? null : _records[index];
    final nameController = TextEditingController(
      text: existing?['name']?.toString() ?? '',
    );
    DateTime? iqama = DateTime.tryParse(existing?['iqama']?.toString() ?? '');
    DateTime? contract = DateTime.tryParse(
      existing?['contract']?.toString() ?? '',
    );
    DateTime? insurance = DateTime.tryParse(
      existing?['insurance']?.toString() ?? '',
    );
    String employeeImage = existing?['imagePath']?.toString() ?? '';
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) {
          Future<void> choose(String type) async {
            final picked = await showDatePicker(
              context: context,
              initialDate: DateTime.now().add(const Duration(days: 30)),
              firstDate: DateTime.now().subtract(const Duration(days: 3650)),
              lastDate: DateTime.now().add(const Duration(days: 7300)),
            );
            if (picked == null) return;
            setDialogState(() {
              if (type == 'iqama') iqama = picked;
              if (type == 'contract') contract = picked;
              if (type == 'insurance') insurance = picked;
            });
          }

          Widget dateTile(
            String title,
            IconData icon,
            DateTime? value,
            String type,
          ) => ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(icon, color: const Color(0xFF7DD3FC)),
            title: Text(title, style: const TextStyle(color: Colors.white)),
            subtitle: Text(
              _format(value),
              style: const TextStyle(color: Colors.white60),
            ),
            trailing: const Icon(
              Icons.calendar_month,
              color: Color(0xFF38BDF8),
            ),
            onTap: () => choose(type),
          );

          return AlertDialog(
            backgroundColor: const Color(0xFF172554),
            title: Text(
              existing == null ? 'إضافة تنبيهات موظف' : 'تعديل تنبيهات موظف',
              style: const TextStyle(color: Colors.white),
            ),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: nameController,
                    style: const TextStyle(color: Colors.white),
                    decoration: InputDecoration(
                      labelText: 'اسم الموظف',
                      labelStyle: const TextStyle(color: Colors.white70),
                      prefixIcon: const Icon(
                        Icons.person_outline,
                        color: Color(0xFF7DD3FC),
                      ),
                      filled: true,
                      fillColor: const Color(0xFF0B1020),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                  dateTile(
                    'انتهاء الإقامة',
                    Icons.badge_outlined,
                    iqama,
                    'iqama',
                  ),
                  dateTile(
                    'انتهاء العقد',
                    Icons.description_outlined,
                    contract,
                    'contract',
                  ),
                  dateTile(
                    'انتهاء التأمين',
                    Icons.health_and_safety_outlined,
                    insurance,
                    'insurance',
                  ),
                  DocumentImagePicker(
                    path: employeeImage,
                    onChanged: (value) =>
                        setDialogState(() => employeeImage = value),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () {
                  if (nameController.text.trim().isEmpty ||
                      iqama == null ||
                      contract == null ||
                      insurance == null) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text('اكتب اسم الموظف وحدد جميع التواريخ'),
                      ),
                    );
                    return;
                  }
                  Navigator.pop(dialogContext, {
                    ...?existing,
                    'id':
                        existing?['id'] ??
                        DateTime.now().microsecondsSinceEpoch.toString(),
                    'imagePath': employeeImage,
                    'name': nameController.text.trim(),
                    'iqama': iqama!.toIso8601String(),
                    'contract': contract!.toIso8601String(),
                    'insurance': insurance!.toIso8601String(),
                  });
                },
                child: const Text('حفظ'),
              ),
            ],
          );
        },
      ),
    );
    await Future<void>.delayed(const Duration(milliseconds: 400));
    nameController.dispose();
    if (result == null || !mounted) return;
    setState(() {
      if (index == null) {
        _records.insert(0, result);
      } else {
        _records[index] = result;
      }
    });
    await _save();
  }

  Future<void> _delete(int index) async {
    final confirmed = await confirmSensitiveDeletion(
      context,
      title: 'حذف تنبيهات الموظف',
      message: 'سيتم حذف سجل ${_records[index]['name']} نهائيًا.',
    );
    if (confirmed != true || !mounted) return;
    setState(() => _records.removeAt(index));
    await _save();
  }

  Widget _dateRow(String title, String value) {
    final color = _dateColor(value);
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        children: [
          Expanded(
            child: Text(title, style: const TextStyle(color: Colors.white70)),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                _format(DateTime.tryParse(value)),
                style: TextStyle(color: color, fontWeight: FontWeight.bold),
              ),
              Text(
                _remaining(value),
                style: TextStyle(color: color, fontSize: 11),
              ),
            ],
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        actions: [PageRefreshButton(onRefresh: _load)],
        title: const Text('تنبيهات الموظفين'),
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _showForm,
        icon: const Icon(Icons.person_add_alt_1),
        label: const Text('إضافة موظف'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _records.isEmpty
          ? const Center(
              child: Text(
                'لا توجد تنبيهات موظفين بعد',
                style: TextStyle(color: Colors.white70, fontSize: 18),
              ),
            )
          : ListView.separated(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 90),
              itemCount: _records.length,
              separatorBuilder: (_, _) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                final record = _records[index];
                return Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFF172554),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Column(
                    children: [
                      Row(
                        children: [
                          if ((record['imagePath']?.toString() ?? '')
                              .isNotEmpty)
                            DocumentImage(
                              path: record['imagePath'].toString(),
                              width: 50,
                              height: 50,
                            )
                          else
                            const CircleAvatar(
                              backgroundColor: Color(0xFF0C4A6E),
                              child: Icon(
                                Icons.person,
                                color: Color(0xFF7DD3FC),
                              ),
                            ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Text(
                              record['name'] as String,
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                          IconButton(
                            tooltip: 'تعديل',
                            onPressed: () => _showForm(index: index),
                            icon: const Icon(
                              Icons.edit_outlined,
                              color: Color(0xFF7DD3FC),
                            ),
                          ),
                          IconButton(
                            onPressed: () => _delete(index),
                            icon: const Icon(
                              Icons.delete_outline,
                              color: Color(0xFFEF4444),
                            ),
                          ),
                        ],
                      ),
                      const Divider(color: Colors.white12),
                      _dateRow('انتهاء الإقامة', record['iqama'] as String),
                      _dateRow('انتهاء العقد', record['contract'] as String),
                      _dateRow('انتهاء التأمين', record['insurance'] as String),
                    ],
                  ),
                );
              },
            ),
    ),
  );
}

class AlertCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;

  const AlertCard({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      color: const Color(0xFF172554),
      margin: const EdgeInsets.only(bottom: 14),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: ListTile(
        onTap: onTap,
        leading: Icon(icon, color: const Color(0xFF38BDF8), size: 30),
        title: Text(
          title,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
          ),
        ),
        subtitle: Text(subtitle, style: const TextStyle(color: Colors.white70)),
        trailing: const Icon(
          Icons.arrow_forward_ios,
          color: Colors.white54,
          size: 18,
        ),
      ),
    );
  }
}

class OrganizationAlertsPage extends StatefulWidget {
  const OrganizationAlertsPage({
    super.key,
    this.categoryId,
    this.recordId,
    this.categoryTitle,
  });
  final String? categoryId;
  final String? recordId;
  final String? categoryTitle;

  @override
  State<OrganizationAlertsPage> createState() => _OrganizationAlertsPageState();
}

class _OrganizationAlertsPageState extends State<OrganizationAlertsPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  static const _recordsKey = 'organization_alert_records';
  static const _categoriesKey = 'organization_alert_categories_v1';
  List<Map<String, String>> _categories = [];
  bool _ready = false;
  final Map<String, String> savedDates = {};

  final List<Map<String, dynamic>> alerts = [
    {
      'id': 'balady',
      'title': 'الرخصة البلدية',
      'icon': Icons.store,
      'website': 'https://balady.gov.sa',
    },
    {
      'id': 'cr',
      'title': 'السجل التجاري',
      'icon': Icons.description,
      'website': 'https://business.sa',
    },
    {
      'id': 'qiwa',
      'title': 'اشتراك قوى',
      'icon': Icons.groups,
      'website': 'https://www.qiwa.sa',
    },
    {
      'id': 'mudad',
      'title': 'حماية الأجور – مدد',
      'icon': Icons.payments,
      'website': 'https://mudad.com.sa',
    },
    {
      'id': 'civil_defense',
      'title': 'شهادة الدفاع المدني',
      'icon': Icons.local_fire_department,
      'website': 'https://salamah.998.gov.sa',
    },
    {
      'id': 'spl',
      'title': 'سبل',
      'icon': Icons.local_shipping_outlined,
      'website': 'https://splonline.com.sa',
    },

    {
      'title': 'فاتورة الكهرباء',
      'type': 'monthlyBill',
      'icon': Icons.electric_bolt,
      'bills': [
        {'month': 'أغسطس 2026', 'amount': 300, 'paid': false},
        {'month': 'ظٹظˆظ„ظٹظˆ 2026', 'amount': 150, 'paid': true},
        {'month': 'ظٹظˆظ†ظٹظˆ 2026', 'amount': 200, 'paid': true},
      ],
    },
  ];
  Future<void> saveElectricityBills(List<dynamic> bills) async {
    final prefs = await _branchPrefs;

    await prefs.setString('electricity_bills', jsonEncode(bills));
    await KhdoomNotifications.syncStoredAlerts();
  }

  Future<void> loadElectricityBills() async {
    final prefs = await _branchPrefs;
    final savedBills = prefs.getString('electricity_bills');

    if (savedBills == null) {
      if (prefs.branchId != BranchPreferences.mainId && mounted) {
        setState(
          () => alerts.firstWhere((a) => a['type'] == 'monthlyBill')['bills'] =
              <dynamic>[],
        );
      }
      return;
    }

    final decodedBills = jsonDecode(savedBills) as List<dynamic>;

    final electricityAlert = alerts.firstWhere(
      (alert) => alert['type'] == 'monthlyBill',
    );

    if (!mounted) return;

    setState(() {
      electricityAlert['bills'] = decodedBills
          .map((bill) => Map<String, dynamic>.from(bill))
          .toList();
    });
  }

  @override
  void initState() {
    super.initState();
    _loadAlertRecords();
    loadElectricityBills();
  }

  IconData _iconFor(String value) =>
      const {
        'store': Icons.store,
        'description': Icons.description,
        'groups': Icons.groups,
        'payments': Icons.payments,
        'fire': Icons.local_fire_department,
        'shipping': Icons.local_shipping_outlined,
        'document': Icons.folder_copy_outlined,
      }[value] ??
      Icons.folder_copy_outlined;

  String _iconName(IconData icon) {
    if (icon == Icons.store) return 'store';
    if (icon == Icons.description) return 'description';
    if (icon == Icons.groups) return 'groups';
    if (icon == Icons.payments) return 'payments';
    if (icon == Icons.local_fire_department) return 'fire';
    if (icon == Icons.local_shipping_outlined) return 'shipping';
    return 'document';
  }

  Future<void> _loadAlertRecords() async {
    final prefs = await _branchPrefs;
    final saved = prefs.getString(_recordsKey);
    if (saved != null && saved.isNotEmpty) {
      try {
        final records = (jsonDecode(saved) as List)
            .map((e) => Map<String, dynamic>.from(e as Map))
            .map(
              (e) => {
                ...e,
                'icon': _iconFor(e['iconName']?.toString() ?? 'document'),
              },
            )
            .toList();
        alerts.removeWhere((item) => item['type'] != 'monthlyBill');
        alerts.insertAll(0, records);
      } catch (_) {}
    } else {
      for (final alert in alerts.where(
        (item) => item['type'] != 'monthlyBill',
      )) {
        final oldDate = prefs.getString('alert_${alert['title']}');
        if (oldDate != null) alert['date'] = oldDate;
      }
      await _saveAlertRecords();
    }
    savedDates.clear();
    for (final alert in alerts.where((item) => item['type'] != 'monthlyBill')) {
      final date = alert['date']?.toString() ?? '';
      if (date.isNotEmpty) savedDates[alert['title'].toString()] = date;
    }
    final rawCategories = prefs.getString(_categoriesKey);
    final savedCategories = rawCategories == null
        ? null
        : (jsonDecode(rawCategories) as List)
              .map((e) => Map<String, String>.from(e as Map))
              .toList();
    _categories = organizationCategories(
      alerts.where((a) => a['type'] != 'monthlyBill'),
      savedCategories,
    );
    if (rawCategories != jsonEncode(_categories)) {
      if (!await prefs.setString(_categoriesKey, jsonEncode(_categories)))
        throw StateError('تعذر حفظ التصنيفات');
    }
    _ready = true;
    if (mounted) setState(() {});
  }

  Future<void> _saveAlertRecords() async {
    final prefs = await _branchPrefs;
    final records = alerts
        .where((item) => item['type'] != 'monthlyBill')
        .map(
          (item) => {
            ...item,
            'iconName': _iconName(
              item['icon'] as IconData? ?? Icons.folder_copy_outlined,
            ),
          }..remove('icon'),
        )
        .toList();
    if (!await prefs.setString(_recordsKey, jsonEncode(records)))
      throw StateError('تعذر حفظ المستندات');
    await KhdoomNotifications.syncStoredAlerts();
  }

  Future<void> _editAlert(Map<String, dynamic>? existing) async {
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => OrganizationRecordEditor(initial: existing),
    );
    if (result == null || !mounted) return;
    result['categoryId'] = existing == null
        ? widget.categoryId
        : organizationCategoryOf(existing);
    result['categoryTitle'] = widget.categoryTitle;
    setState(() {
      if (existing == null) {
        alerts.insert(alerts.length - 1, result);
      } else {
        alerts[alerts.indexOf(existing)] = result;
      }
      savedDates.clear();
      for (final alert in alerts.where(
        (item) => item['type'] != 'monthlyBill',
      )) {
        final date = alert['date']?.toString() ?? '';
        if (date.isNotEmpty) savedDates[alert['title'].toString()] = date;
      }
    });
    await _saveAlertRecords();
  }

  Future<void> _editCategory([Map<String, String>? category]) async {
    final name = await showDialog<String>(
      context: context,
      builder: (_) =>
          OrganizationCategoryNameDialog(initial: category?['title']),
    );
    if (name == null || !mounted) return;
    if (_categories.any(
      (c) => c['title'] == name && c['id'] != category?['id'],
    )) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('يوجد تصنيف بهذا الاسم')));
      return;
    }
    final updated = [for (final c in _categories) Map<String, String>.of(c)];
    if (category == null) {
      updated.add({
        'id': 'category_${DateTime.now().microsecondsSinceEpoch}',
        'title': name,
      });
    } else {
      updated.firstWhere((c) => c['id'] == category['id'])['title'] = name;
    }
    final prefs = await _branchPrefs;
    if (!await prefs.setString(_categoriesKey, jsonEncode(updated))) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('تعذر حفظ التصنيف')));
      return;
    }
    if (mounted) setState(() => _categories = updated);
  }

  Future<void> _deleteCategory(Map<String, String> category) async {
    if (alerts.any(
      (a) =>
          a['type'] != 'monthlyBill' &&
          organizationCategoryOf(a) == category['id'],
    )) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'التصنيف يحتوي مستندات؛ احذف المستندات المطلوبة من داخله أولًا',
          ),
        ),
      );
      return;
    }
    final confirmed = await confirmSensitiveDeletion(
      context,
      title: 'حذف التصنيف',
      message: 'سيتم حذف التصنيف الفارغ ${category['title']}.',
    );
    if (confirmed != true || !mounted) return;
    final updated = _categories
        .where((c) => c['id'] != category['id'])
        .toList();
    final prefs = await _branchPrefs;
    if (!await prefs.setString(_categoriesKey, jsonEncode(updated))) return;
    if (mounted) setState(() => _categories = updated);
  }

  Widget _categoriesPage() => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        title: const Text('تنبيهات المؤسسة'),
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
        actions: [PageRefreshButton(onRefresh: _loadAlertRecords)],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _ready ? () => _editCategory() : null,
        icon: const Icon(Icons.create_new_folder_outlined),
        label: const Text('إضافة تصنيف'),
      ),
      body: !_ready
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
              children: [
                for (final category in _categories)
                  Card(
                    color: const Color(0xFF172554),
                    child: ListTile(
                      leading: const Icon(
                        Icons.folder_outlined,
                        color: Color(0xFF38BDF8),
                      ),
                      title: Text(
                        category['title']!,
                        style: const TextStyle(color: Colors.white),
                      ),
                      subtitle: Text(
                        '${alerts.where((a) => a['type'] != 'monthlyBill' && organizationCategoryOf(a) == category['id']).length} مستند',
                        style: const TextStyle(color: Colors.white70),
                      ),
                      trailing: PopupMenuButton<String>(
                        color: const Color(0xFF172554),
                        iconColor: Colors.white70,
                        onSelected: (action) => action == 'edit'
                            ? _editCategory(category)
                            : _deleteCategory(category),
                        itemBuilder: (_) => [
                          const PopupMenuItem(
                            value: 'edit',
                            child: Text(
                              'تعديل اسم التصنيف',
                              style: TextStyle(color: Colors.white),
                            ),
                          ),
                          const PopupMenuItem(
                            value: 'delete',
                            child: Text(
                              'حذف التصنيف الفارغ',
                              style: TextStyle(color: Colors.white),
                            ),
                          ),
                        ],
                      ),
                      onTap: () async {
                        await Navigator.push<void>(
                          context,
                          MaterialPageRoute(
                            builder: (_) => OrganizationAlertsPage(
                              categoryId: category['id'],
                              categoryTitle: category['title'],
                            ),
                          ),
                        );
                        await _loadAlertRecords();
                      },
                    ),
                  ),
                Card(
                  color: const Color(0xFF172554),
                  child: ListTile(
                    leading: const Icon(
                      Icons.electric_bolt,
                      color: Colors.amber,
                    ),
                    title: const Text(
                      'فاتورة الكهرباء',
                      style: TextStyle(color: Colors.white),
                    ),
                    onTap: () => Navigator.push<void>(
                      context,
                      MaterialPageRoute(
                        builder: (_) => const OrganizationAlertsPage(
                          categoryId: '_electricity',
                          categoryTitle: 'فواتير الكهرباء',
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
    ),
  );

  Future<void> _deleteAlert(Map<String, dynamic> alert) async {
    final confirmed = await confirmSensitiveDeletion(
      context,
      title: 'حذف ${alert['title']}',
      message: 'سيتم حذف التنبيه وصورته وبياناته من خدووم.',
    );
    if (confirmed != true || !mounted) return;
    setState(() => alerts.remove(alert));
    await _saveAlertRecords();
  }

  Future<void> loadSavedDates() async {
    final prefs = await _branchPrefs;

    for (final alert in alerts) {
      final title = alert['title'] as String;
      final date = prefs.getString('alert_$title');

      if (date != null) {
        savedDates[title] = date;
      }
    }

    if (mounted) {
      setState(() {});
    }
  }

  Future<void> selectDate(String title) async {
    final oldDate = DateTime.tryParse(savedDates[title] ?? '');

    final selectedDate = await showDatePicker(
      context: context,
      initialDate: oldDate ?? DateTime.now(),
      firstDate: DateTime(2020),
      lastDate: DateTime(2040),
      helpText: 'اختر تاريخ الانتهاء',
      cancelText: 'إلغاء',
      confirmText: 'حفظ',
    );

    if (selectedDate == null) return;

    final savedDate = selectedDate.toIso8601String();
    final prefs = await _branchPrefs;

    await prefs.setString('alert_$title', savedDate);
    await KhdoomNotifications.syncStoredAlerts();

    if (!mounted) return;

    setState(() {
      savedDates[title] = savedDate;
    });

    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text('تم حفظ تاريخ $title')));
  }

  String formatDate(String? savedDate) {
    if (savedDate == null) {
      return 'اضغط لإضافة تاريخ الانتهاء';
    }

    final date = DateTime.tryParse(savedDate);

    if (date == null) {
      return 'اضغط لإضافة تاريخ الانتهاء';
    }

    final day = date.day.toString().padLeft(2, '0');
    final month = date.month.toString().padLeft(2, '0');

    return 'تاريخ الانتهاء: $day/$month/${date.year}';
  }

  Color alertColor(String? savedDate) {
    if (savedDate == null) {
      return const Color(0xFF38BDF8);
    }

    final date = DateTime.tryParse(savedDate);

    if (date == null) {
      return const Color(0xFF38BDF8);
    }

    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final remainingDays = date.difference(today).inDays;

    if (remainingDays < 0) {
      return Colors.red;
    }

    if (remainingDays <= 30) {
      return Colors.orange;
    }

    return Colors.green;
  }

  @override
  Widget build(BuildContext context) {
    if (widget.categoryId == null) return _categoriesPage();
    final visibleAlerts = alerts
        .where(
          (a) => widget.categoryId == '_electricity'
              ? a['type'] == 'monthlyBill'
              : a['type'] != 'monthlyBill' &&
                    organizationCategoryOf(a) == widget.categoryId,
        )
        .where(
          (a) =>
              widget.recordId == null || a['id'].toString() == widget.recordId,
        )
        .toList();
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [
            PageRefreshButton(
              onRefresh: () async {
                await _loadAlertRecords();
                await loadElectricityBills();
              },
            ),
          ],
          title: Text(widget.categoryTitle ?? 'مستندات المؤسسة'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
        ),
        floatingActionButton: widget.categoryId == '_electricity'
            ? null
            : FloatingActionButton.extended(
                onPressed: _ready ? () => _editAlert(null) : null,
                icon: const Icon(Icons.add),
                label: const Text('إضافة مستند'),
              ),
        body: ListView.builder(
          padding: const EdgeInsets.all(16),
          itemCount: _ready ? visibleAlerts.length : 0,
          itemBuilder: (context, index) {
            final alert = visibleAlerts[index];
            final title = alert['title'] as String;
            final savedDate = alert['date']?.toString();
            final color = alertColor(savedDate);

            return Card(
              color: const Color(0xFF172554),
              margin: const EdgeInsets.only(bottom: 12),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(16),
                side: BorderSide(color: color.withValues(alpha: 0.50)),
              ),
              child: ListTile(
                onTap: () {
                  if (alert['type'] == 'monthlyBill') {
                    final bills = List<Map<String, dynamic>>.from(
                      alert['bills'] ?? [],
                    );

                    showDialog(
                      context: context,
                      builder: (context) => StatefulBuilder(
                        builder: (context, setDialogState) => AlertDialog(
                          backgroundColor: const Color(0xFF172554),
                          insetPadding: const EdgeInsets.symmetric(
                            horizontal: 12,
                            vertical: 24,
                          ),
                          contentPadding: const EdgeInsets.fromLTRB(
                            12,
                            12,
                            12,
                            8,
                          ),
                          title: const Text(
                            'فواتير الكهرباء الشهرية',
                            style: TextStyle(color: Colors.white),
                          ),
                          content: SizedBox(
                            width: double.maxFinite,
                            height: MediaQuery.sizeOf(context).height * 0.62,
                            child: ListView.separated(
                              shrinkWrap: true,
                              itemCount: bills.length,
                              separatorBuilder: (_, _) => const Divider(),
                              itemBuilder: (context, billIndex) {
                                final bill = bills[billIndex];
                                final paid = bill['paid'] == true;

                                return ListTile(
                                  contentPadding: const EdgeInsets.symmetric(
                                    horizontal: 4,
                                    vertical: 4,
                                  ),
                                  minLeadingWidth: 0,
                                  onTap: () {
                                    final monthController =
                                        TextEditingController(
                                          text: bill['month'].toString(),
                                        );

                                    final amountController =
                                        TextEditingController(
                                          text: bill['amount'].toString(),
                                        );
                                    bool selectedPaid = bill['paid'] == true;
                                    final dueDateController =
                                        TextEditingController(
                                          text:
                                              bill['dueDate']?.toString() ?? '',
                                        );
                                    showDialog(
                                      context: context,
                                      builder: (editContext) => AlertDialog(
                                        backgroundColor: const Color(
                                          0xFF172554,
                                        ),
                                        title: const Text(
                                          'تعديل الفاتورة',
                                          style: TextStyle(color: Colors.white),
                                        ),
                                        content: Column(
                                          mainAxisSize: MainAxisSize.min,
                                          children: [
                                            TextField(
                                              controller: monthController,
                                              style: const TextStyle(
                                                color: Colors.white,
                                              ),
                                              decoration: const InputDecoration(
                                                labelText: 'الشهر والتاريخ',
                                                labelStyle: TextStyle(
                                                  color: Colors.cyanAccent,
                                                ),
                                              ),
                                            ),
                                            const SizedBox(height: 12),
                                            TextField(
                                              controller: amountController,
                                              keyboardType:
                                                  TextInputType.number,
                                              style: const TextStyle(
                                                color: Colors.white,
                                              ),
                                              decoration: const InputDecoration(
                                                labelText: 'المبلغ بالريال',
                                                labelStyle: TextStyle(
                                                  color: Colors.cyanAccent,
                                                ),
                                              ),
                                            ),
                                            const SizedBox(height: 12),
                                            TextField(
                                              controller: dueDateController,
                                              readOnly: true,
                                              style: const TextStyle(
                                                color: Colors.white,
                                              ),
                                              decoration: const InputDecoration(
                                                labelText: 'تاريخ الاستحقاق',
                                                labelStyle: TextStyle(
                                                  color: Colors.cyanAccent,
                                                ),
                                                suffixIcon: Icon(
                                                  Icons.event_outlined,
                                                ),
                                              ),
                                              onTap: () async {
                                                final current =
                                                    DateTime.tryParse(
                                                      dueDateController.text,
                                                    ) ??
                                                    DateTime.now().add(
                                                      const Duration(days: 30),
                                                    );
                                                final picked =
                                                    await showDatePicker(
                                                      context: editContext,
                                                      initialDate: current,
                                                      firstDate: DateTime.now(),
                                                      lastDate: DateTime.now()
                                                          .add(
                                                            const Duration(
                                                              days: 3650,
                                                            ),
                                                          ),
                                                    );
                                                if (picked != null) {
                                                  dueDateController.text =
                                                      picked.toIso8601String();
                                                }
                                              },
                                            ),
                                            const SizedBox(height: 12),
                                            DropdownButtonFormField<bool>(
                                              initialValue: selectedPaid,
                                              dropdownColor: const Color(
                                                0xFF172554,
                                              ),
                                              style: const TextStyle(
                                                color: Colors.white,
                                              ),
                                              decoration: const InputDecoration(
                                                labelText: 'حالة الفاتورة',
                                                labelStyle: TextStyle(
                                                  color: Colors.cyanAccent,
                                                ),
                                              ),
                                              items: const [
                                                DropdownMenuItem(
                                                  value: false,
                                                  child: Text('يرجى السداد'),
                                                ),
                                                DropdownMenuItem(
                                                  value: true,
                                                  child: Text('تم السداد'),
                                                ),
                                              ],
                                              onChanged: (value) {
                                                if (value != null) {
                                                  selectedPaid = value;
                                                }
                                              },
                                            ),
                                          ],
                                        ),
                                        actions: [
                                          TextButton.icon(
                                            icon: const Icon(
                                              Icons.delete,
                                              color: Colors.redAccent,
                                            ),
                                            label: const Text(
                                              'حذف',
                                              style: TextStyle(
                                                color: Colors.redAccent,
                                              ),
                                            ),
                                            onPressed: () async {
                                              final confirm =
                                                  await confirmSensitiveDeletion(
                                                    editContext,
                                                    title: 'حذف الفاتورة',
                                                    message: 'سيتم حذف هذه الفاتورة نهائيًا.',
                                                  );

                                              if (confirm == true) {
                                                setDialogState(() {
                                                  bills.removeAt(billIndex);
                                                });

                                                saveElectricityBills(bills);
                                                if (!editContext.mounted) {
                                                  return;
                                                }
                                                Navigator.pop(editContext);
                                              }
                                            },
                                          ),

                                          TextButton(
                                            onPressed: () =>
                                                Navigator.pop(editContext),
                                            child: const Text('إلغاء'),
                                          ),
                                          ElevatedButton(
                                            onPressed: () {
                                              final newAmount = double.tryParse(
                                                amountController.text.trim(),
                                              );

                                              final dueDate = DateTime.tryParse(
                                                dueDateController.text.trim(),
                                              );
                                              if (newAmount == null ||
                                                  dueDate == null) {
                                                return;
                                              }

                                              setState(() {
                                                bill['month'] = monthController
                                                    .text
                                                    .trim();
                                                bill['amount'] = newAmount;
                                                bill['paid'] = selectedPaid;
                                                bill['dueDate'] = dueDate
                                                    .toIso8601String();
                                              });

                                              saveElectricityBills(bills);

                                              Navigator.pop(editContext);
                                              Navigator.pop(context);
                                            },
                                            child: const Text('حفظ'),
                                          ),
                                        ],
                                      ),
                                    );
                                  },

                                  title: Text(
                                    bill['month'].toString(),
                                    style: const TextStyle(color: Colors.white),
                                  ),
                                  subtitle: Text(
                                    (bill['dueDate']?.toString() ?? '').isEmpty
                                        ? '${bill['amount']} ريال'
                                        : '${bill['amount']} ريال\n${formatDate(bill['dueDate']?.toString())}',
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      color: Colors.cyanAccent,
                                    ),
                                  ),
                                  trailing: ConstrainedBox(
                                    constraints: const BoxConstraints(
                                      maxWidth: 132,
                                    ),
                                    child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Flexible(
                                          child: Text(
                                            paid ? 'تم السداد' : 'يرجى السداد',
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis,
                                            style: TextStyle(
                                              color: paid
                                                  ? Colors.green
                                                  : Colors.orange,
                                              fontWeight: FontWeight.bold,
                                            ),
                                          ),
                                        ),
                                        IconButton(
                                          icon: const Icon(
                                            Icons.delete,
                                            color: Colors.redAccent,
                                          ),
                                          onPressed: () async {
                                            final confirm =
                                                await confirmSensitiveDeletion(
                                                  context,
                                                  title: 'حذف الفاتورة',
                                                  message: 'سيتم حذف هذه الفاتورة نهائيًا.',
                                                );

                                            if (confirm == true) {
                                              setDialogState(() {
                                                bills.removeAt(billIndex);
                                              });

                                              saveElectricityBills(bills);
                                            }
                                          },
                                        ),
                                      ],
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
                          actions: [
                            ElevatedButton.icon(
                              icon: const Icon(Icons.add),
                              label: const Text('إضافة فاتورة'),
                              onPressed: () {
                                final monthController = TextEditingController();
                                final amountController =
                                    TextEditingController();
                                bool newPaid = false;
                                final dueDateController =
                                    TextEditingController();

                                showDialog(
                                  context: context,
                                  builder: (addContext) => StatefulBuilder(
                                    builder: (addContext, setAddState) =>
                                        AlertDialog(
                                          backgroundColor: const Color(
                                            0xFF172554,
                                          ),
                                          title: const Text(
                                            'إضافة فاتورة جديدة',
                                            style: TextStyle(
                                              color: Colors.white,
                                            ),
                                          ),
                                          content: Column(
                                            mainAxisSize: MainAxisSize.min,
                                            children: [
                                              TextField(
                                                controller: monthController,
                                                style: const TextStyle(
                                                  color: Colors.white,
                                                ),
                                                decoration:
                                                    const InputDecoration(
                                                      labelText:
                                                          'الشهر والتاريخ',
                                                      labelStyle: TextStyle(
                                                        color:
                                                            Colors.cyanAccent,
                                                      ),
                                                    ),
                                              ),
                                              const SizedBox(height: 12),
                                              TextField(
                                                controller: amountController,
                                                keyboardType:
                                                    TextInputType.number,
                                                style: const TextStyle(
                                                  color: Colors.white,
                                                ),
                                                decoration:
                                                    const InputDecoration(
                                                      labelText:
                                                          'المبلغ بالريال',
                                                      labelStyle: TextStyle(
                                                        color:
                                                            Colors.cyanAccent,
                                                      ),
                                                    ),
                                              ),
                                              const SizedBox(height: 12),
                                              TextField(
                                                controller: dueDateController,
                                                readOnly: true,
                                                style: const TextStyle(
                                                  color: Colors.white,
                                                ),
                                                decoration:
                                                    const InputDecoration(
                                                      labelText:
                                                          'تاريخ الاستحقاق',
                                                      labelStyle: TextStyle(
                                                        color:
                                                            Colors.cyanAccent,
                                                      ),
                                                      suffixIcon: Icon(
                                                        Icons.event_outlined,
                                                      ),
                                                    ),
                                                onTap: () async {
                                                  final picked =
                                                      await showDatePicker(
                                                        context: addContext,
                                                        initialDate:
                                                            DateTime.now().add(
                                                              const Duration(
                                                                days: 30,
                                                              ),
                                                            ),
                                                        firstDate:
                                                            DateTime.now(),
                                                        lastDate: DateTime.now()
                                                            .add(
                                                              const Duration(
                                                                days: 3650,
                                                              ),
                                                            ),
                                                      );
                                                  if (picked != null) {
                                                    dueDateController.text =
                                                        picked
                                                            .toIso8601String();
                                                  }
                                                },
                                              ),
                                              const SizedBox(height: 12),
                                              DropdownButtonFormField<bool>(
                                                initialValue: newPaid,
                                                dropdownColor: const Color(
                                                  0xFF172554,
                                                ),
                                                style: const TextStyle(
                                                  color: Colors.white,
                                                ),
                                                decoration:
                                                    const InputDecoration(
                                                      labelText:
                                                          'حالة الفاتورة',
                                                      labelStyle: TextStyle(
                                                        color:
                                                            Colors.cyanAccent,
                                                      ),
                                                    ),
                                                items: const [
                                                  DropdownMenuItem(
                                                    value: false,
                                                    child: Text('يرجى السداد'),
                                                  ),
                                                  DropdownMenuItem(
                                                    value: true,
                                                    child: Text('تم السداد'),
                                                  ),
                                                ],
                                                onChanged: (value) {
                                                  if (value != null) {
                                                    setAddState(() {
                                                      newPaid = value;
                                                    });
                                                  }
                                                },
                                              ),
                                            ],
                                          ),
                                          actions: [
                                            TextButton(
                                              onPressed: () =>
                                                  Navigator.pop(addContext),
                                              child: const Text('إلغاء'),
                                            ),
                                            ElevatedButton(
                                              onPressed: () {
                                                final newAmount =
                                                    double.tryParse(
                                                      amountController.text
                                                          .trim(),
                                                    );

                                                final dueDate =
                                                    DateTime.tryParse(
                                                      dueDateController.text
                                                          .trim(),
                                                    );
                                                if (monthController.text
                                                        .trim()
                                                        .isEmpty ||
                                                    newAmount == null ||
                                                    dueDate == null) {
                                                  return;
                                                }

                                                setDialogState(() {
                                                  bills.insert(0, {
                                                    'month': monthController
                                                        .text
                                                        .trim(),
                                                    'amount': newAmount,
                                                    'paid': newPaid,
                                                    'dueDate': dueDate
                                                        .toIso8601String(),
                                                    'id': DateTime.now()
                                                        .microsecondsSinceEpoch
                                                        .toString(),
                                                  });
                                                });

                                                saveElectricityBills(bills);
                                                Navigator.pop(addContext);
                                              },
                                              child: const Text('حفظ'),
                                            ),
                                          ],
                                        ),
                                  ),
                                );
                              },
                            ),
                          ],
                        ),
                      ),
                    );
                  } else {
                    Navigator.push<void>(
                      context,
                      MaterialPageRoute(
                        builder: (_) => RecordInformationPage(record: alert),
                      ),
                    );
                  }
                },
                leading: (alert['imagePath']?.toString() ?? '').isNotEmpty
                    ? ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: DocumentImage(
                          path: alert['imagePath'].toString(),
                          width: 48,
                          height: 48,
                          fit: BoxFit.cover,
                          errorBuilder: (_, _, _) => Icon(
                            alert['icon'] as IconData,
                            color: color,
                            size: 28,
                          ),
                        ),
                      )
                    : Icon(alert['icon'] as IconData, color: color, size: 28),
                title: Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                subtitle: Text(
                  alert['type'] == 'monthlyBill'
                      ? ((alert['bills'] as List).isEmpty
                            ? 'لا توجد فواتير'
                            : '${(alert['bills'] as List).first['amount']} ريال - يرجى السداد')
                      : formatDate(savedDate),
                  style: TextStyle(
                    color: alert['type'] == 'monthlyBill'
                        ? Colors.orange
                        : color,
                  ),
                ),
                trailing: alert['type'] == 'monthlyBill'
                    ? Icon(Icons.edit_calendar, color: color)
                    : Wrap(
                        spacing: 0,
                        children: [
                          IconButton(
                            tooltip: 'تعديل',
                            onPressed: () => _editAlert(alert),
                            icon: Icon(Icons.edit_outlined, color: color),
                          ),
                          IconButton(
                            tooltip: 'حذف',
                            onPressed: () => _deleteAlert(alert),
                            icon: const Icon(
                              Icons.delete_outline,
                              color: Colors.redAccent,
                            ),
                          ),
                        ],
                      ),
              ), // ListTile
            ); // Card
          }, // itemBuilder
        ), // ListView.builder
      ), // Scaffold
    ); // Directionality
  }
}

class AiTrainingPage extends StatefulWidget {
  const AiTrainingPage({super.key});

  @override
  State<AiTrainingPage> createState() => _AiTrainingPageState();
}

class _AiTrainingPageState extends State<AiTrainingPage> {
  final _controller = TextEditingController();
  final _messageController = TextEditingController();
  final Map<String, String> _saved = {};
  final List<Map<String, dynamic>> _trainingMessages = [];
  String _trainerReply =
      'اسألني أو اشرح لي شغلك. للحفظ اكتب «احفظ:» ثم المعلومة، أو استخدم تعليم بالسؤال والجواب.';
  String? _pendingFact;
  String _type = 'shared';
  bool _loading = true;
  bool _saving = false;

  static const _types = <String, String>{
    'shared': 'معلومات مشتركة لجميع الموظفين',
    'assistant': 'مساعد المؤسسة الرئيسي',
    'chat': 'تعليم الدردشة السابق (أرشيف)',
    'reception': 'موظف استقبال العملاء',
    'whatsapp': 'موظف واتساب',
    'calls': 'موظف الاتصالات',
    'commercial_research': 'موظف البحث التجاري',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<KhdoomCloudApi> _api() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) {
      throw const CloudApiException(401, 'سجل الدخول أولًا لحفظ التدريب');
    }
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  Future<void> _load({bool preserveDraft = false}) async {
    try {
      final api = await _api();
      try {
        final rows = await api.aiTraining();
        final messages = await api.aiTrainingMessages();
        _trainingMessages
          ..clear()
          ..addAll(
            messages.map((raw) => Map<String, dynamic>.from(raw as Map)),
          );
        for (final raw in rows) {
          final row = Map<String, dynamic>.from(raw as Map);
          _saved[row['employee_type'].toString()] =
              row['content']?.toString() ?? '';
        }
        if (!preserveDraft) _controller.text = _saved[_type] ?? '';
      } finally {
        api.close();
      }
    } on CloudApiException catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final api = await _api();
      try {
        await api.saveAiTraining(_type, _controller.text.trim());
        _saved[_type] = _controller.text.trim();
      } finally {
        api.close();
      }
      if (mounted)
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('تم حفظ تدريب الموظف ✓')));
    } on CloudApiException catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _sendTrainingMessage() async {
    final message = _messageController.text.trim();
    if (message.isEmpty || _saving) return;
    _messageController.clear();
    setState(() {
      _saving = true;
      _trainingMessages.add({
        'employee_type': _type,
        'sender': 'owner',
        'message': message,
      });
    });
    try {
      final api = await _api();
      try {
        final response = await api.aiTrainingChat(_type, message);
        final reply = response['text']?.toString() ?? 'لم أستطع تجهيز الرد.';
        if (mounted) {
          setState(() {
            _trainerReply = reply;
            _trainingMessages.add({
              'employee_type': _type,
              'sender': 'assistant',
              'message': reply,
            });
          });
        }
        if (response['action'] == 'saved' ||
            response['action'] == 'already_saved') {
          final hasDraft = _controller.text != (_saved[_type] ?? '');
          await _load(preserveDraft: hasDraft);
        }
      } finally {
        api.close();
      }
    } on CloudApiException catch (error) {
      if (mounted) setState(() => _trainerReply = error.message);
    } on SocketException {
      if (mounted) setState(() => _trainerReply = 'تعذر الاتصال بخادم خدووم');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    _messageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(
        title: const Text('تدريب موظفي AI'),
        actions: [
          PageRefreshButton(onRefresh: () => _load(preserveDraft: true)),
        ],
        backgroundColor: const Color(0xFF111B35),
        foregroundColor: Colors.white,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'للرد على الزبائن اختر «موظف استقبال العملاء». تعليم الدردشة السابق محفوظ في الأرشيف ويظل مستخدمًا. «مساعد المؤسسة» مستقل لشغلك الداخلي. لا تضع كلمات مرور أو بيانات بنكية.',
                  style: TextStyle(color: Colors.white70, height: 1.5),
                ),
                FilledButton.icon(
                  onPressed: _saving
                      ? null
                      : () async {
                          await Navigator.push(
                            context,
                            MaterialPageRoute(
                              builder: (_) => QuestionAnswerTrainingPage(
                                initialEmployee: _type,
                              ),
                            ),
                          );
                          if (mounted) await _load(preserveDraft: true);
                        },
                  icon: const Icon(Icons.quiz_outlined),
                  label: const Text('تعليم بالسؤال والجواب'),
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: _type,
                  dropdownColor: const Color(0xFF172554),
                  style: const TextStyle(color: Colors.white),
                  decoration: const InputDecoration(
                    labelText: 'الموظف',
                    filled: true,
                    fillColor: Color(0xFF172554),
                  ),
                  items: _types.entries
                      .map(
                        (entry) => DropdownMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                      )
                      .toList(),
                  onChanged: (value) {
                    if (value == null) return;
                    _saved[_type] = _controller.text;
                    setState(() {
                      _type = value;
                      _controller.text = _saved[value] ?? '';
                    });
                  },
                ),
                const SizedBox(height: 16),
                Container(
                  width: double.infinity,
                  constraints: const BoxConstraints(minHeight: 150),
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFF172554),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0xFF38BDF8)),
                  ),
                  child: Builder(
                    builder: (_) {
                      final messages = _trainingMessages
                          .where((item) => item['employee_type'] == _type)
                          .toList();
                      if (messages.isEmpty) {
                        return Text(
                          _trainerReply,
                          style: const TextStyle(
                            color: Colors.white,
                            height: 1.6,
                          ),
                        );
                      }
                      final visible = messages.skip(
                        messages.length > 12 ? messages.length - 12 : 0,
                      );
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: visible.map((item) {
                          final owner = item['sender'] == 'owner';
                          return Align(
                            alignment: owner
                                ? Alignment.centerRight
                                : Alignment.centerLeft,
                            child: Container(
                              margin: const EdgeInsets.only(bottom: 8),
                              padding: const EdgeInsets.all(10),
                              decoration: BoxDecoration(
                                color: owner
                                    ? const Color(0xFF075985)
                                    : const Color(0xFF1E293B),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Text(
                                item['message']?.toString() ?? '',
                                style: const TextStyle(color: Colors.white),
                              ),
                            ),
                          );
                        }).toList(),
                      );
                    },
                  ),
                ),
                if (_pendingFact != null) ...[
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: () {
                            _messageController.text = 'احفظها';
                            _sendTrainingMessage();
                          },
                          icon: const Icon(Icons.save),
                          label: const Text('حفظ المعلومة'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: OutlinedButton(
                          onPressed: () {
                            _messageController.text = 'لا تحفظ';
                            _sendTrainingMessage();
                          },
                          child: const Text('إلغاء'),
                        ),
                      ),
                    ],
                  ),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  children: [
                    ActionChip(
                      label: const Text('علّمه معلومة'),
                      onPressed: () =>
                          setState(() => _messageController.text = 'احفظ: '),
                    ),
                    ActionChip(
                      label: const Text('وش يعرف؟'),
                      onPressed: () {
                        _messageController.text = 'وش تعرف؟';
                        _sendTrainingMessage();
                      },
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                TextField(
                  controller: _messageController,
                  maxLines: 4,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _sendTrainingMessage(),
                  style: const TextStyle(color: Colors.white),
                  decoration: InputDecoration(
                    hintText: 'اكتب معلومة، أو اسأل: وش تعرف؟',
                    hintStyle: const TextStyle(color: Colors.white38),
                    filled: true,
                    fillColor: const Color(0xFF172554),
                    border: const OutlineInputBorder(),
                    suffixIcon: IconButton(
                      onPressed: _saving ? null : _sendTrainingMessage,
                      icon: const Icon(Icons.send, color: Color(0xFF38BDF8)),
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                const Text(
                  'المعلومات التي تؤكد حفظها تبقى محفوظة على الخادم ولا تختفي عند إغلاق التطبيق.',
                  style: TextStyle(color: Colors.white54),
                ),
              ],
            ),
    ),
  );
}

class AiEmployeesPage extends StatelessWidget {
  final String subscriptionPackage;

  const AiEmployeesPage({super.key, required this.subscriptionPackage});

  Future<void> _showUpgradeDialog(
    BuildContext context,
    String employeeName,
    String requiredPackage,
  ) async {
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.lock_outline, color: Color(0xFFF59E0B)),
            SizedBox(width: 10),
            Text('ميزة تحتاج ترقية'),
          ],
        ),
        content: Text(
          '$employeeName متاح ضمن $requiredPackage. يمكنك استعراض الباقات والترقية لتفعيل هذه الميزة.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('لاحقًا'),
          ),
          FilledButton.icon(
            onPressed: () {
              Navigator.pop(dialogContext);
              Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => const SubscriptionPackagesPage(),
                ),
              );
            },
            icon: const Icon(Icons.workspace_premium_outlined),
            label: const Text('عرض الباقات'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final includesReception =
        subscriptionPackage == 'basic' || subscriptionPackage == 'vip';
    final includesAllEmployees = subscriptionPackage == 'vip';
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          title: const Text(
            'موظفو AI',
            style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
          ),
          iconTheme: const IconThemeData(color: Colors.white),
        ),
        body: Padding(
          padding: const EdgeInsets.all(16),
          child: ListView(
            children: [
              AiEmployeeCard(
                icon: Icons.auto_awesome,
                title: 'تدريب موظف خدووم',
                subtitle: 'نشاط المؤسسة وخدماتها وسياسات الرد الخاصة بها',
                onTap: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const AiEmployeeTrainingPage(),
                    ),
                  );
                },
              ),
              const SizedBox(height: 12),
              AiEmployeeCard(
                icon: Icons.school_outlined,
                title: 'تدريب موظفي AI',
                subtitle: 'معلومات مشتركة وتدريب مستقل لكل موظف',
                onTap: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(builder: (_) => const AiTrainingPage()),
                  );
                },
              ),
              const SizedBox(height: 12),
              AiEmployeeCard(
                icon: Icons.support_agent,
                title: 'موظف استقبال العملاء',
                subtitle: includesReception
                    ? 'دردشة الزبائن والأسعار والمواعيد والتحويل لموظف بشري'
                    : 'متاح في الباقة الأساسية وVIP',
                locked: !includesReception,
                onTap: includesReception
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const ReceptionEmployeePage(),
                          ),
                        );
                      }
                    : () => _showUpgradeDialog(
                        context,
                        'موظف استقبال العملاء',
                        'الباقة الأساسية وVIP',
                      ),
              ),
              const SizedBox(height: 12),
              AiEmployeeCard(
                icon: Icons.chat,
                title: 'موظف واتساب',
                subtitle: includesAllEmployees
                    ? 'الرد على الرسائل ومتابعة العملاء'
                    : 'متاح في باقة VIP',
                locked: !includesAllEmployees,
                onTap: includesAllEmployees
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) =>
                                const WhatsAppWorkspace(inbox: true),
                          ),
                        );
                      }
                    : () => _showUpgradeDialog(
                        context,
                        'موظف واتساب',
                        'باقة VIP',
                      ),
              ),
              const SizedBox(height: 12),
              AiEmployeeCard(
                icon: Icons.phone_in_talk,
                title: 'موظف الاتصالات',
                subtitle: includesAllEmployees
                    ? 'استقبال المكالمات وجمع بيانات العميل'
                    : 'متاح في باقة VIP',
                locked: !includesAllEmployees,
                onTap: includesAllEmployees
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const CallsEmployeePage(),
                          ),
                        );
                      }
                    : () => _showUpgradeDialog(
                        context,
                        'موظف الاتصالات',
                        'باقة VIP',
                      ),
              ),
              const SizedBox(height: 12),
              AiEmployeeCard(
                icon: Icons.search,
                title: 'موظف البحث التجاري',
                subtitle: includesAllEmployees
                    ? 'البحث عن مقاولين وعملاء وأسعار السوق'
                    : 'متاح في باقة VIP',
                locked: !includesAllEmployees,
                onTap: includesAllEmployees
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) =>
                                const CommercialResearchEmployeePage(),
                          ),
                        );
                      }
                    : () => _showUpgradeDialog(
                        context,
                        'موظف البحث التجاري',
                        'باقة VIP',
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class KhdoomAiAssistantPage extends StatefulWidget {
  const KhdoomAiAssistantPage({super.key});

  @override
  State<KhdoomAiAssistantPage> createState() => _KhdoomAiAssistantPageState();
}

class _KhdoomAiAssistantPageState extends State<KhdoomAiAssistantPage> {
  final _conversation = AssistantConversation();
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  final _messageController = TextEditingController();
  final _scrollController = ScrollController();
  final Map<int, List<Map<String, dynamic>>> _messageAttachments = {};
  final List<({String text, bool fromUser})> _messages = [
    (
      text: 'مرحبًا 👋 أنا موظف خدوم الذكي. اسألني عن استخدام التطبيق، الباقات، الموظفين، المركبات، الفواتير، التنبيهات أو أكواد التفعيل.',
      fromUser: false,
    ),
  ];

  @override
  void dispose() {
    _messageController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _sendMessage([String? suggested]) async {
    final question = (suggested ?? _messageController.text).trim();
    if (question.isEmpty || _conversation.busy) return;
    _messageController.clear();
    setState(() {
      _messages.add((text: question, fromUser: true));
    });
    final attachments = <Map<String, dynamic>>[];
    final answer = await _answer(question, attachments: attachments);
    _conversation.remember(question, answer);
    if (!mounted) return;
    setState(() {
      _messageAttachments[_messages.length] = attachments;
      _messages.add((text: answer, fromUser: false));
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  String _normalizeArabic(String input) => input
      .toLowerCase()
      .replaceAll(RegExp(r'[أإآ]'), 'ا')
      .replaceAll('ة', 'ه')
      .replaceAll('ظ‰', 'ظٹ')
      .replaceAll(RegExp(r'[\u064B-\u065F\u0670]'), '')
      .replaceAll(RegExp(r'[^\u0600-\u06FFa-z0-9 ]'), ' ')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();

  bool _hasAny(String text, List<String> words) => words.any(text.contains);

  Future<String> _answer(
    String question, {
    List<Map<String, dynamic>>? attachments,
  }) async {
    final text = _normalizeArabic(question);
    final prefs = await _branchPrefs;
    _conversation.bind(prefs.branchId);
    if (_hasAny(text, ['فرع', 'فروع'])) {
      return 'الفرع الحالي: ${prefs.branchName}. للتبديل افتح الإعدادات ← إدارة الفروع. تبي معلومات هذا الفرع أو طريقة إضافة فرع؟';
    }
    final organizationRecords = KhdoomNotifications._storedList(
      prefs,
      'organization_alert_records',
    );
    final vehicles = KhdoomNotifications._storedList(
      prefs,
      'business_vehicles',
    );
    final employees = KhdoomNotifications._storedList(
      prefs,
      'business_employees',
    );
    final employeeAlerts = KhdoomNotifications._storedList(
      prefs,
      'employee_alert_records',
    );
    final appointments = KhdoomNotifications._storedList(
      prefs,
      'business_appointments_requests',
    );
    final wantsAdd = _hasAny(text, ['اضف', 'اضاف', 'انشئ', 'جديد', 'اسجل']);
    final wantsDelete = _hasAny(text, ['احذف', 'حذف', 'ازاله', 'الغاء']);
    final wantsView = _hasAny(text, ['اشوف', 'عرض', 'اظهر', 'اين', 'وين']);

    if (_hasAny(text, ['مقاس', 'مقاسات', 'صيان', 'صيانه'])) {
      final wantsMaintenance = _hasAny(text, ['صيان', 'صيانه']);
      final typeWord = wantsMaintenance ? 'صيان' : 'مقاس';
      final matching = appointments.where((item) {
        final type = _normalizeArabic(item['type']?.toString() ?? '');
        return type.contains(typeWord) && item['status'] != 'completed';
      }).toList();
      if (_hasAny(text, ['كم', 'عدد'])) {
        final label = wantsMaintenance ? 'مواعيد الصيانة' : 'مواعيد المقاسات';
        return matching.isEmpty
            ? 'لا توجد $label معلقة.'
            : 'عدد $label المعلقة: ${matching.length}.';
      }
      if (matching.isEmpty) {
        return wantsMaintenance
            ? 'لا توجد مواعيد صيانة معلقة.'
            : 'لا توجد مواعيد مقاسات معلقة.';
      }
      return matching
          .take(8)
          .map((item) {
            final customer = item['customer']?.toString() ?? 'عميل غير محدد';
            final title =
                item['title']?.toString() ?? item['type']?.toString() ?? 'موعد';
            final date = item['date']?.toString() ?? 'تاريخ غير محدد';
            return 'â€¢ $title â€” $customer\n  $date';
          })
          .join('\n');
    }

    Map<String, dynamic>? matchedEmployee;
    for (final employee in employees) {
      final name = _normalizeArabic(employee['name']?.toString() ?? '');
      if (name.isNotEmpty && text.contains(name)) {
        matchedEmployee = employee;
        break;
      }
    }
    if (matchedEmployee != null) {
      attachments?.add(matchedEmployee);
      final name = matchedEmployee['name']?.toString() ?? 'الموظف';
      Map<String, dynamic>? alert;
      for (final item in employeeAlerts) {
        if (_normalizeArabic(item['name']?.toString() ?? '') ==
            _normalizeArabic(name)) {
          alert = item;
          break;
        }
      }
      if (alert != null) attachments?.add(alert);
      return [
        'بيانات الموظف $name:',
        'الوظيفة: ${matchedEmployee['role'] ?? 'غير محددة'}',
        if ((matchedEmployee['phone']?.toString() ?? '').isNotEmpty)
          'الجوال: ${matchedEmployee['phone']}',
        'الحالة: ${matchedEmployee['active'] == false ? 'موقوف' : 'نشط'}',
        if (alert != null) 'انتهاء الإقامة: ${alert['iqama'] ?? 'غير مضاف'}',
        if (alert != null) 'انتهاء العقد: ${alert['contract'] ?? 'غير مضاف'}',
        if (alert != null)
          'انتهاء التأمين: ${alert['insurance'] ?? 'غير مضاف'}',
      ].join('\n');
    }
    if (_hasAny(text, ['كم موظف', 'عدد الموظفين', 'كم عامل', 'عدد العمال'])) {
      return employees.length == 1
          ? 'لديك موظف واحد مسجل.'
          : 'لديك ${employees.length} موظفين مسجلين.';
    }

    Map<String, dynamic>? matchedRecord;
    for (final record in organizationRecords) {
      final title = _normalizeArabic(record['title']?.toString() ?? '');
      final aliases = title
          .split(' ')
          .where((word) => word.length >= 3)
          .toList();
      if ((title.isNotEmpty &&
              (text.contains(title) || title.contains(text))) ||
          aliases.any(text.contains)) {
        matchedRecord = record;
        break;
      }
    }
    if (matchedRecord != null) {
      attachments?.add(matchedRecord);
      final title = matchedRecord['title']?.toString() ?? 'المستند';
      final details = matchedRecord['details']?.toString().trim() ?? '';
      final date = DateTime.tryParse(matchedRecord['date']?.toString() ?? '');
      final website = matchedRecord['website']?.toString().trim() ?? '';
      final dateText = date == null
          ? 'تاريخ الانتهاء غير مضاف'
          : 'تاريخ الانتهاء: ${date.day}/${date.month}/${date.year}';
      return [
        'بيانات $title:',
        if (details.isNotEmpty) details,
        dateText,
        if (website.isNotEmpty) 'الموقع الرسمي: $website',
        'يمكنك التعديل أو إضافة صورة من «التنبيهات ← تنبيهات المؤسسة».',
      ].join('\n');
    }

    if (_hasAny(text, ['فاتور', 'فواتير', 'كهرب', 'سداد', 'مدفوع'])) {
      if (wantsAdd) {
        return 'لإضافة فاتورة: افتح «التنبيهات ← فواتير الكهرباء الشهرية»، واضغط «إضافة فاتورة»، ثم اكتب الشهر والمبلغ وحدد حالة السداد واحفظ.';
      }
      if (wantsDelete) {
        return 'لحذف فاتورة: افتح «التنبيهات ← فواتير الكهرباء الشهرية»، واضغط رمز الحذف بجانب الفاتورة ثم أكد. حساب الموظف يحتاج الصلاحية المناسبة.';
      }
      return 'الفواتير موجودة داخل «التنبيهات ← فواتير الكهرباء الشهرية». تستطيع إضافة الشهر والمبلغ وحالة السداد، ثم تعديل الفاتورة أو حذفها. اكتب «كيف أضيف فاتورة؟» للحصول على الخطوات.';
    }
    if (_hasAny(text, ['اعلان', 'اعلانات', 'ترويج'])) {
      if (wantsAdd) {
        return 'إنشاء الإعلان متاح لـVIP: افتح «الإعدادات ← الباقات ← إدارة إعلاني»، أدخل العنوان والتفاصيل والتواصل ثم أرسل. يصل للمالك للموافقة، وبعد القبول يُنشر 48 ساعة.';
      }
      if (wantsView) {
        return 'الإعلان المقبول يظهر كشريط أسفل خيارات الرئيسية للمجانية والأساسية. اضغط عليه لعرض المؤسسة والتفاصيل ورقم التواصل. VIP لا تظهر له الإعلانات.';
      }
      return 'نظام الإعلان: مشترك VIP ينشئه، والمالك يراجعه، ثم يظهر للمجانية والأساسية في شريط الرئيسية ويُحذف تلقائيًا بعد 48 ساعة من الموافقة.';
    }
    if (_hasAny(text, ['مركب', 'سيار', 'لوحه', 'استمار', 'فحص', 'تامين'])) {
      if (wantsAdd) {
        return 'افتح «المركبات ← إضافة مركبة»، واكتب الاسم واللوحة وحدد انتهاء الاستمارة والفحص والتأمين. المجانية مركبة واحدة، الأساسية 5، وVIP دون حد.';
      }
      if (wantsDelete) {
        return 'لحذف مركبة افتح قسم المركبات واضغط الحذف في بطاقتها ثم أكد. حساب الموظف يحتاج صلاحية حذف المركبات.';
      }
      if (vehicles.isEmpty) {
        return 'لا توجد مركبات مسجلة حاليًا. افتح «المركبات ← إضافة مركبة» لإضافة بياناتها وصورتها.';
      }
      if (_hasAny(text, ['كم', 'عدد'])) {
        return vehicles.length == 1
            ? 'لديك مركبة واحدة مسجلة.'
            : 'لديك ${vehicles.length} مركبات مسجلة.';
      }
      final matching = vehicles.where((vehicle) {
        final name = _normalizeArabic(vehicle['name']?.toString() ?? '');
        final plate = _normalizeArabic(vehicle['plate']?.toString() ?? '');
        final assigned = _normalizeArabic(
          vehicle['assignedEmployee']?.toString() ?? '',
        );
        final status = vehicle['status']?.toString() ?? 'working';
        final statusMatches =
            (text.contains('صيان') && status == 'maintenance') ||
            (text.contains('عطل') && status == 'broken') ||
            (text.contains('متوقف') && status == 'stopped');
        return (name.isNotEmpty && text.contains(name)) ||
            (plate.isNotEmpty && text.contains(plate)) ||
            (assigned.isNotEmpty && text.contains(assigned)) ||
            statusMatches;
      }).toList();
      final selected = matching.isEmpty ? vehicles.take(5) : matching;
      attachments?.addAll(selected);
      return selected
          .map((vehicle) {
            final name = vehicle['name']?.toString() ?? 'مركبة';
            final plate = vehicle['plate']?.toString() ?? 'غير مضافة';
            final employee = vehicle['assignedEmployee']?.toString() ?? '';
            final status =
                const {
                  'working': 'تعمل',
                  'stopped': 'متوقفة',
                  'broken': 'عطلانة',
                  'maintenance': 'تحت الصيانة',
                }[vehicle['status']] ??
                'تعمل';
            return '🚗 $name\nاللوحة: $plate\nالموظف المسؤول: ${employee.isEmpty ? 'غير محدد' : employee}\nالحالة: $status\nالاستمارة: ${vehicle['registration'] ?? 'غير مضافة'}\nالفحص: ${vehicle['inspection'] ?? 'غير مضاف'}\nالتأمين: ${vehicle['insurance'] ?? 'غير مضاف'}';
          })
          .join('\n\n');
    }
    if (_hasAny(text, [
      'موظف البحث',
      'بحث تجاري',
      'مقاول',
      'عميل محتمل',
      'اسعار السوق',
    ])) {
      return 'موظف البحث التجاري ضمن VIP. يبحث عن المقاولين والعملاء والأسعار، ويكتب تقرير متابعة يتضمن طريقة الاتصال والنتيجة والسعر واحتمالية الموافقة والخطوة التالية.';
    }
    if (_hasAny(text, ['موظف استقبال', 'استقبال العملاء'])) {
      return 'موظف الاستقبال متاح في الأساسية وVIP. تضبط معلومات المؤسسة وساعات العمل وأسلوب الرد ليجمع بيانات العملاء ويرد على الاستفسارات الأولية.';
    }
    if (_hasAny(text, ['واتس', 'واتساب', 'مكالم', 'اتصالات'])) {
      return 'موظفا واتساب والاتصالات ضمن VIP. واتساب يحتاج Meta Business وخادمًا عامًا، والاتصالات تحتاج مزود اتصال سحابي.';
    }
    if (_hasAny(text, ['موظف', 'موظفين', 'صلاحيات', 'مستخدم'])) {
      if (wantsAdd) {
        return 'من «الإعدادات ← الموظفون والصلاحيات» اضغط إضافة، واكتب البيانات وكلمة مرور 8 خانات وحدد الصلاحيات. المجانية موظف واحد، الأساسية 5، وVIP دون حد.';
      }
      return 'الموظفون العاديون حسابات لفريق المؤسسة، أما موظفو AI فهم مساعدين ذكيين. المجانية تشمل اسأل موظف، الأساسية تضيف الاستقبال، وVIP يفتح الجميع.';
    }
    if (_hasAny(text, [
      'باقه',
      'باقات',
      'اشتراك',
      'vip',
      'في اي بي',
      'اساسيه',
      'مجانيه',
      'ترقيه',
    ])) {
      return _conversation.ask(prefs, question);
    }
    if (_hasAny(text, ['كود', 'تفعيل', 'رمز الاشتراك'])) {
      return 'لتفعيل كود: من صفحة الدخول اختر تفعيل الكود، اكتب اسم المستخدم والكود وأكد. يحدد الكود الباقة والمدة، وبعد انتهائها يعود الحساب للمجانية.';
    }
    if (_hasAny(text, [
      'شعار',
      'ظ„ظˆظ‚ظˆ',
      'بيانات المؤسسه',
      'اسم المؤسسه',
      'نشاط المؤسسه',
    ])) {
      return 'افتح «مؤسستي» لتعديل الاسم والنشاط والتواصل، أو اختر «إضافة شعار المؤسسة». بعد الحفظ يظهر الشعار تلقائيًا قبل الترحيب في الرئيسية.';
    }
    if (_hasAny(text, ['تنبيه', 'موعد', 'مواعيد', 'تجديد', 'انتهاء'])) {
      return 'قسم التنبيهات يجمع المواعيد وتجديدات المؤسسة والموظفين والمركبات والفواتير. فعّل الإشعارات والتذكيرات من الإعدادات.';
    }
    if (_hasAny(text, [
      'دخول',
      'تسجيل',
      'اسم المستخدم',
      'كلمه المرور',
      'نسيت',
      'حساب',
    ])) {
      return 'استخدم اسم المستخدم وكلمة المرور في صفحة الدخول. عند النسيان اختر استعادة الاسم أو كلمة المرور، ويمكن للمدير تغيير بيانات الدخول من الإعدادات.';
    }
    if (_hasAny(text, ['خادم', 'سيرفر', 'اجهزه', 'جهاز ثاني', 'مزامنه'])) {
      return 'يمكن استخدام خدووم على عدة أجهزة متصلة بالخادم نفسه. للاستخدام خارج الشبكة المحلية نحتاج نشر الخادم على رابط HTTPS عام.';
    }
    if (_hasAny(text, ['سلام', 'مرحبا', 'هلا', 'اهلا'])) {
      return 'أهلًا بك 🌟 اسألني عن الفواتير، الإعلانات، المركبات، الموظفين، موظفي AI، الباقات، التنبيهات أو بيانات المؤسسة.';
    }
    if (_hasAny(text, ['مساعده', 'ساعدني', 'مميزات', 'وش تعرف'])) {
      return 'أشرح لك بيانات المؤسسة، الموظفين، المركبات، الفواتير، التنبيهات، الباقات والأكواد، الإعلانات، موظفي AI، الدخول والخادم. اكتب اسم الميزة فقط أو سؤالك كاملًا.';
    }
    return _conversation.ask(prefs, question);
  }

  @override
  Widget build(BuildContext context) {
    const suggestions = [
      'الفواتير',
      'الإعلانات',
      'المركبات',
      'موظفو AI',
      'الباقات',
      'بيانات المؤسسة',
    ];
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('موظف خدوم AI'),
          actions: const [
            Padding(
              padding: EdgeInsetsDirectional.only(end: 16),
              child: Icon(Icons.smart_toy, color: Color(0xFF38BDF8)),
            ),
          ],
        ),
        body: SafeArea(
          child: Column(
            children: [
              SizedBox(
                height: 48,
                child: ListView.separated(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 7,
                  ),
                  scrollDirection: Axis.horizontal,
                  itemCount: suggestions.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 8),
                  itemBuilder: (_, index) => ActionChip(
                    label: Text(suggestions[index]),
                    onPressed: () => _sendMessage(suggestions[index]),
                  ),
                ),
              ),
              Expanded(
                child: ListView.builder(
                  controller: _scrollController,
                  padding: const EdgeInsets.all(14),
                  itemCount: _messages.length,
                  itemBuilder: (_, index) {
                    final message = _messages[index];
                    return Align(
                      alignment: message.fromUser
                          ? Alignment.centerRight
                          : Alignment.centerLeft,
                      child: Container(
                        constraints: const BoxConstraints(maxWidth: 330),
                        margin: const EdgeInsets.only(bottom: 10),
                        padding: const EdgeInsets.symmetric(
                          horizontal: 15,
                          vertical: 12,
                        ),
                        decoration: BoxDecoration(
                          color: message.fromUser
                              ? const Color(0xFF0284C7)
                              : const Color(0xFF172554),
                          borderRadius: BorderRadius.circular(16),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SelectableText(
                              message.text,
                              style: const TextStyle(
                                color: Colors.white,
                                height: 1.45,
                              ),
                            ),
                            RecordAttachments(
                              records: _messageAttachments[index] ?? const [],
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
              Container(
                padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
                color: const Color(0xFF111B35),
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _messageController,
                        style: const TextStyle(color: Colors.white),
                        textInputAction: TextInputAction.send,
                        onSubmitted: (_) => _sendMessage(),
                        decoration: InputDecoration(
                          hintText: 'اكتب استفسارك هنا...',
                          hintStyle: const TextStyle(color: Colors.white54),
                          filled: true,
                          fillColor: const Color(0xFF172554),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(14),
                            borderSide: BorderSide.none,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filled(
                      onPressed: _sendMessage,
                      icon: const Icon(Icons.send),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class VipAdvertisementDialog extends StatefulWidget {
  final String initialTitle;
  final String initialMessage;
  final String initialContact;
  final bool initialEnabled;

  const VipAdvertisementDialog({
    super.key,
    required this.initialTitle,
    required this.initialMessage,
    required this.initialContact,
    required this.initialEnabled,
  });

  @override
  State<VipAdvertisementDialog> createState() => _VipAdvertisementDialogState();
}

class _VipAdvertisementDialogState extends State<VipAdvertisementDialog> {
  late final TextEditingController _titleController;
  late final TextEditingController _messageController;
  late final TextEditingController _contactController;
  late bool _enabled;

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(text: widget.initialTitle);
    _messageController = TextEditingController(text: widget.initialMessage);
    _contactController = TextEditingController(text: widget.initialContact);
    _enabled = widget.initialEnabled;
  }

  @override
  void dispose() {
    _titleController.dispose();
    _messageController.dispose();
    _contactController.dispose();
    super.dispose();
  }

  InputDecoration _decoration(String label, IconData icon) => InputDecoration(
    labelText: label,
    labelStyle: const TextStyle(color: Colors.white70),
    prefixIcon: Icon(icon, color: const Color(0xFFFBBF24)),
    filled: true,
    fillColor: const Color(0xFF0B1020),
    border: OutlineInputBorder(
      borderRadius: BorderRadius.circular(12),
      borderSide: BorderSide.none,
    ),
  );

  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: const Text('إدارة إعلان VIP', style: TextStyle(color: Colors.white)),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _titleController,
            style: const TextStyle(color: Colors.white),
            decoration: _decoration('عنوان الإعلان', Icons.title),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _messageController,
            maxLines: 3,
            style: const TextStyle(color: Colors.white),
            decoration: _decoration('نص الإعلان', Icons.campaign_outlined),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _contactController,
            keyboardType: TextInputType.phone,
            style: const TextStyle(color: Colors.white),
            decoration: _decoration('رقم التواصل', Icons.phone_outlined),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text(
              'تشغيل الإعلان',
              style: TextStyle(color: Colors.white),
            ),
            value: _enabled,
            onChanged: (value) => setState(() => _enabled = value),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('إلغاء'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, {
          'title': _titleController.text.trim(),
          'message': _messageController.text.trim(),
          'contact': _contactController.text.trim(),
          'enabled': _enabled,
        }),
        child: const Text('إرسال للمراجعة'),
      ),
    ],
  );
}

class CommercialResearchEmployeePage extends StatefulWidget {
  const CommercialResearchEmployeePage({super.key});

  @override
  State<CommercialResearchEmployeePage> createState() =>
      _CommercialResearchEmployeePageState();
}

class _CommercialResearchEmployeePageState
    extends State<CommercialResearchEmployeePage> {
  final _formKey = GlobalKey<FormState>();
  final _keywordsController = TextEditingController();
  final _cityController = TextEditingController();
  String _researchType = 'عملاء محتملون';
  bool _isLoading = true;
  bool _isSearching = false;
  String? _researchError;
  String? _aiResearchText;
  List<Map<String, dynamic>> _researchResults = [];
  List<CommercialLead> _savedLeads = [];

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await BranchPreferences.getInstance();
    _keywordsController.text =
        prefs.getString('commercial_research_keywords') ?? '';
    _cityController.text = prefs.getString('commercial_research_city') ?? '';
    final storedLeads =
        prefs.getStringList('commercial_research_leads') ?? const <String>[];
    final restoredLeads = <CommercialLead>[];
    for (final item in storedLeads) {
      try {
        restoredLeads.add(CommercialLead.decode(item));
      } catch (_) {
        // تجاهل السجل التالف فقط مع المحافظة على بقية الفرص.
      }
    }
    if (!mounted) return;
    setState(() {
      _researchType =
          prefs.getString('commercial_research_type') ?? 'عملاء محتملون';
      _savedLeads = restoredLeads;
      _isLoading = false;
    });
  }

  Future<void> _persistCommercialLeads() async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setStringList(
      'commercial_research_leads',
      _savedLeads.map((lead) => lead.encode()).toList(),
    );
  }

  Future<void> _saveResultAsLead(Map<String, dynamic> result) async {
    final contractor = TextEditingController(
      text: result['name']?.toString() ?? '',
    );
    final phone = TextEditingController();
    final facade = TextEditingController(text: 'زجاج وواجهات');
    final measurements = TextEditingController();
    final notes = TextEditingController();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: const Color(0xFF111B35),
        title: const Text(
          'حفظ فرصة تجارية',
          style: TextStyle(color: Colors.white),
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: contractor,
                style: const TextStyle(color: Colors.white),
                decoration: _researchDecoration(
                  'المقاول أو جهة المشروع',
                  Icons.business,
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: phone,
                keyboardType: TextInputType.phone,
                style: const TextStyle(color: Colors.white),
                decoration: _researchDecoration(
                  'رقم واتساب إن وجد',
                  Icons.phone,
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: facade,
                style: const TextStyle(color: Colors.white),
                decoration: _researchDecoration(
                  'نوع الواجهة أو الخدمة',
                  Icons.view_quilt_outlined,
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: measurements,
                style: const TextStyle(color: Colors.white),
                decoration: _researchDecoration(
                  'المقاسات أو المساحة التقديرية',
                  Icons.straighten,
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: notes,
                maxLines: 3,
                style: const TextStyle(color: Colors.white),
                decoration: _researchDecoration(
                  'ملاحظات عن المشروع',
                  Icons.notes,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                'المقاسات المأخوذة من الإنترنت تقديرية حتى استلام المخطط أو معاينة الموقع.',
                style: TextStyle(color: Color(0xFFFBBF24), height: 1.4),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('إلغاء'),
          ),
          FilledButton.icon(
            onPressed: () => Navigator.pop(dialogContext, true),
            icon: const Icon(Icons.save),
            label: const Text('حفظ الفرصة'),
          ),
        ],
      ),
    );
    if (accepted != true || !mounted) {
      contractor.dispose();
      phone.dispose();
      facade.dispose();
      measurements.dispose();
      notes.dispose();
      return;
    }
    final now = DateTime.now();
    final phoneText = phone.text.trim();
    final lead = CommercialLead(
      id: now.microsecondsSinceEpoch.toString(),
      organizationId: 'local',
      projectName: result['name']?.toString() ?? 'فرصة تجارية',
      city: _cityController.text.trim(),
      location: result['address']?.toString() ?? '',
      contractor: contractor.text.trim(),
      facadeType: facade.text.trim(),
      estimatedMeasurements: measurements.text.trim(),
      measurementsAreEstimated: true,
      status: phoneText.isEmpty
          ? CommercialLeadStatus.discovered
          : CommercialLeadStatus.readyToContact,
      contacts: phoneText.isEmpty
          ? const []
          : [
              CommercialContact(
                company: contractor.text.trim(),
                phone: phoneText,
                whatsApp: phoneText,
              ),
            ],
      sources: [
        CommercialSource(
          title: result['source']?.toString() ?? 'OpenStreetMap',
          url: '',
          collectedAt: now,
        ),
      ],
      createdAt: now,
      updatedAt: now,
      notes: notes.text.trim(),
    );
    setState(() => _savedLeads.insert(0, lead));
    await _persistCommercialLeads();
    contractor.dispose();
    phone.dispose();
    facade.dispose();
    measurements.dispose();
    notes.dispose();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          phoneText.isEmpty
              ? 'تم حفظ الفرصة وتحتاج إضافة رقم للتواصل'
              : 'تم حفظ الفرصة وهي جاهزة لرسالة تعريفية بلا سعر',
        ),
      ),
    );
  }

  Future<void> _deleteCommercialLead(CommercialLead lead) async {
    setState(() => _savedLeads.removeWhere((item) => item.id == lead.id));
    await _persistCommercialLeads();
  }

  Future<void> _sendLeadIntroduction(CommercialLead lead) async {
    await showCommercialWhatsApp(
      context,
      projectName: lead.projectName,
      phone: lead.contacts.isEmpty ? '' : lead.contacts.first.whatsApp,
    );
  }

  Future<void> _createPriceApproval(CommercialLead lead) async {
    final amount = TextEditingController();
    final details = TextEditingController(
      text: lead.facadeType.isEmpty
          ? 'توريد وتركيب زجاج وواجهات'
          : lead.facadeType,
    );
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: const Color(0xFF111B35),
        title: const Text(
          'طلب موافقة على السعر',
          style: TextStyle(color: Colors.white),
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: amount,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              style: const TextStyle(color: Colors.white),
              decoration: _researchDecoration(
                'السعر المقترح بالريال',
                Icons.payments_outlined,
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: details,
              maxLines: 3,
              style: const TextStyle(color: Colors.white),
              decoration: _researchDecoration(
                'تفاصيل العرض',
                Icons.description_outlined,
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              'لن يُرسل السعر إلى العميل قبل اعتماده.',
              style: TextStyle(color: Color(0xFFFBBF24)),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () {
              final value = double.tryParse(amount.text.trim());
              if (value == null || value <= 0) return;
              Navigator.pop(dialogContext, true);
            },
            child: const Text('إرسال للموافقة'),
          ),
        ],
      ),
    );
    if (accepted != true || !mounted) {
      amount.dispose();
      details.dispose();
      return;
    }
    final now = DateTime.now();
    final draft = CommercialPriceDraft(
      id: now.microsecondsSinceEpoch.toString(),
      amount: double.parse(amount.text.trim()),
      currency: 'SAR',
      details: details.text.trim(),
      createdAt: now,
      status: PriceApprovalStatus.pending,
    );
    final map = lead.toJson();
    map['priceDrafts'] = [
      ...lead.priceDrafts.map((item) => item.toJson()),
      draft.toJson(),
    ];
    map['status'] = CommercialLeadStatus.pricePendingApproval.name;
    map['updatedAt'] = now.toIso8601String();
    final updated = CommercialLead.fromJson(map);
    setState(() {
      final index = _savedLeads.indexWhere((item) => item.id == lead.id);
      if (index >= 0) _savedLeads[index] = updated;
    });
    await _persistCommercialLeads();
    amount.dispose();
    details.dispose();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('تم حفظ السعر وهو بانتظار موافقتك')),
    );
  }

  Future<void> _reviewPrice(
    CommercialLead lead,
    CommercialPriceDraft draft,
    bool approve,
  ) async {
    final now = DateTime.now();
    final reviewed = draft.copyWith(
      status: approve
          ? PriceApprovalStatus.approved
          : PriceApprovalStatus.rejected,
      reviewedAt: now,
    );
    final map = lead.toJson();
    map['priceDrafts'] = lead.priceDrafts
        .map((item) => item.id == draft.id ? reviewed.toJson() : item.toJson())
        .toList();
    map['status'] = approve
        ? CommercialLeadStatus.priceApproved.name
        : CommercialLeadStatus.replied.name;
    map['updatedAt'] = now.toIso8601String();
    final updated = CommercialLead.fromJson(map);
    setState(() {
      final index = _savedLeads.indexWhere((item) => item.id == lead.id);
      if (index >= 0) _savedLeads[index] = updated;
    });
    await _persistCommercialLeads();
  }

  Future<void> _runResearch() async {
    if (!_formKey.currentState!.validate()) return;
    final prefs = await BranchPreferences.getInstance();
    await Future.wait([
      prefs.setString(
        'commercial_research_keywords',
        _keywordsController.text.trim(),
      ),
      prefs.setString('commercial_research_city', _cityController.text.trim()),
      prefs.setString('commercial_research_type', _researchType),
    ]);
    if (!mounted) return;
    setState(() {
      _isSearching = true;
      _researchError = null;
      _aiResearchText = null;
      _researchResults = [];
    });

    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 12);
    try {
      final smartText = await _requestAiCommercialResearch();
      if (smartText != null && smartText.isNotEmpty && mounted) {
        setState(() => _aiResearchText = smartText);
      }
      final decoded = await _searchCommercialPlaces(client);
      if (!mounted) return;
      setState(() {
        _researchResults = decoded
            .whereType<Map>()
            .map(
              (item) => {
                'name': item['name']?.toString().trim().isNotEmpty == true
                    ? item['name'].toString()
                    : item['display_name'].toString().split(',').first.trim(),
                'address': item['display_name']?.toString() ?? '',
                'category': item['type']?.toString() ?? 'نشاط تجاري',
                'phone': item['phone'] ?? '',
                'whatsapp': item['whatsapp'] ?? item['phone'] ?? '',
                'source': 'OpenStreetMap',
              },
            )
            .toList();
        if (_researchResults.isEmpty) {
          _researchError = _researchType == 'أسعار السوق'
              ? 'لم نجد أسعارًا موثقة لهذا البحث. الأسعار تختلف حسب المقاس والمواصفات؛ اطلب عروض أسعار من الموردين قبل اعتمادها.'
              : 'لم نجد أنشطة مطابقة في بيانات الخرائط. جرّب اسم النشاط بدل وصف طويل، مثل «زجاج» أو «مطاعم».';
        }
      });
    } on TimeoutException {
      if (!mounted) return;
      setState(() {
        _researchError =
            'استغرق مصدر البيانات وقتًا طويلًا. حاول مرة أخرى بعد قليل.';
      });
    } on HttpException catch (error) {
      if (!mounted) return;
      setState(() => _researchError = error.message);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _researchError =
            'تعذر جلب البيانات الآن. تحقق من اتصال الإنترنت ثم حاول مرة أخرى.';
      });
    } finally {
      client.close(force: true);
      if (mounted) setState(() => _isSearching = false);
    }
  }

  Future<String?> _requestAiCommercialResearch() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return null;
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      final result = await api.commercialResearch(
        keywords: _keywordsController.text.trim(),
        city: _cityController.text.trim(),
        researchType: _researchType,
      );
      return result['text']?.toString().trim();
    } on CloudApiException catch (error) {
      if (error.statusCode == 403 || error.statusCode == 429) {
        return 'تنبيه: ${error.message}';
      }
      return null;
    } catch (_) {
      return null;
    } finally {
      api.close();
    }
  }

  Future<List<dynamic>> _searchCommercialPlaces(HttpClient client) async {
    final city = _cityController.text.trim();
    final cityUri = Uri.https('nominatim.openstreetmap.org', '/search', {
      'q': '$city, السعودية',
      'format': 'jsonv2',
      'limit': '1',
      'countrycodes': 'sa',
      'accept-language': 'ar,en',
    });
    final cityPayload = await _getJson(client, cityUri);
    if (cityPayload is! List || cityPayload.isEmpty) {
      throw const HttpException(
        'لم نتمكن من تحديد المدينة. اكتب اسم مدينة سعودية واضحًا.',
      );
    }
    final cityResult = Map<String, dynamic>.from(cityPayload.first as Map);

    // OSM category phrases are more reliable in English than in Arabic.
    final mappedTerm = _mappedCommercialTerm();
    if (mappedTerm != null) {
      final categoryUri = Uri.https('nominatim.openstreetmap.org', '/search', {
        'q': '$mappedTerm, $city, Saudi Arabia',
        'format': 'jsonv2',
        'addressdetails': '1',
        'limit': '12',
        'countrycodes': 'sa',
        'accept-language': 'ar,en',
      });
      final categoryPayload = await _getJson(client, categoryUri);
      if (categoryPayload is List && categoryPayload.isNotEmpty) {
        return categoryPayload;
      }
    }
    final latitude = double.tryParse(cityResult['lat']?.toString() ?? '');
    final longitude = double.tryParse(cityResult['lon']?.toString() ?? '');
    if (latitude == null || longitude == null) {
      throw const FormatException('إحداثيات المدينة غير صالحة');
    }

    final query = _buildOverpassQuery(latitude, longitude);
    final overpassUri = Uri.https('overpass-api.de', '/api/interpreter', {
      'data': query,
    });
    final overpassPayload = await _getJson(
      client,
      overpassUri,
      timeout: const Duration(seconds: 35),
    );
    if (overpassPayload is! Map || overpassPayload['elements'] is! List) {
      throw const FormatException('صيغة استجابة غير متوقعة');
    }

    final seen = <String>{};
    final results = <Map<String, dynamic>>[];
    for (final raw in overpassPayload['elements'] as List) {
      if (raw is! Map || raw['tags'] is! Map) continue;
      final tags = Map<String, dynamic>.from(raw['tags'] as Map);
      final name = (tags['name:ar'] ?? tags['name'] ?? '').toString().trim();
      if (name.isEmpty || !seen.add(name.toLowerCase())) continue;
      final category =
          (tags['shop'] ??
                  tags['office'] ??
                  tags['craft'] ??
                  tags['amenity'] ??
                  'نشاط تجاري')
              .toString();
      final addressParts = <String>[
        if ((tags['addr:street'] ?? '').toString().isNotEmpty)
          tags['addr:street'].toString(),
        if ((tags['addr:district'] ?? '').toString().isNotEmpty)
          tags['addr:district'].toString(),
        if ((tags['addr:city'] ?? '').toString().isNotEmpty)
          tags['addr:city'].toString(),
      ];
      results.add({
        'name': name,
        'display_name': addressParts.isEmpty ? city : addressParts.join('، '),
        'type': category,
        'phone': tags['contact:phone'] ?? tags['phone'] ?? '',
        'whatsapp':
            tags['contact:whatsapp'] ??
            tags['whatsapp'] ??
            tags['contact:phone'] ??
            tags['phone'] ??
            '',
      });
      if (results.length == 12) break;
    }
    return results;
  }

  String? _mappedCommercialTerm() {
    final text = _keywordsController.text.trim().toLowerCase();
    if (text.contains('زجاج') || text.contains('مرايا')) return 'glass';
    if (text.contains('مطعم') || text.contains('اكل')) return 'restaurant';
    if (text.contains('قهوة') ||
        text.contains('كافيه') ||
        text.contains('ظ…ظ‚ظ‡ظ‰')) {
      return 'cafe';
    }
    if (text.contains('فندق') || text.contains('شقق')) return 'hotel';
    if (text.contains('صيدلي')) return 'pharmacy';
    if (text.contains('سيار')) return 'car repair';
    if (text.contains('عقار')) return 'estate agent';
    if (text.contains('مقاول')) return 'contractor';
    if (text.contains('بناء') || text.contains('انشاء')) return 'construction';
    return null;
  }

  String _buildOverpassQuery(double latitude, double longitude) {
    final text = _keywordsController.text.trim().toLowerCase();
    const radius = 30000;
    final filters = <String>[];
    if (text.contains('زجاج') || text.contains('مرايا')) {
      filters.addAll(['[shop=glass]', '[craft=glaziery]']);
    } else if (text.contains('مطعم') || text.contains('اكل')) {
      filters.addAll(['[amenity=restaurant]', '[amenity=fast_food]']);
    } else if (text.contains('قهوة') || text.contains('كافيه')) {
      filters.add('[amenity=cafe]');
    } else if (text.contains('سيار')) {
      filters.addAll(['[shop=car]', '[shop=car_repair]']);
    } else if (text.contains('عقار')) {
      filters.add('[office=estate_agent]');
    } else if (text.contains('مقاول') || text.contains('مورد')) {
      filters.addAll(['[office=company]', '[craft][name]']);
    }
    if (filters.isEmpty) {
      filters.addAll(['[shop][name]', '[office][name]', '[craft][name]']);
    }
    final statements = filters
        .map(
          (filter) => 'nwr(around:$radius,$latitude,$longitude)$filter[name];',
        )
        .join();
    return '[out:json][timeout:25];($statements);out center tags 60;';
  }

  Future<dynamic> _getJson(
    HttpClient client,
    Uri uri, {
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final request = await client.getUrl(uri).timeout(timeout);
    request.headers.set(
      HttpHeaders.userAgentHeader,
      'KhdoomBusinessAssistant/1.2 (commercial research)',
    );
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    final response = await request.close().timeout(timeout);
    final body = await utf8.decoder.bind(response).join().timeout(timeout);
    if (response.statusCode == 429) {
      throw const HttpException(
        'مصدر البيانات مشغول حاليًا. انتظر دقيقة ثم أعد المحاولة.',
      );
    }
    if (response.statusCode != 200) {
      throw const HttpException('تعذر الوصول إلى مصدر البيانات الآن.');
    }
    return jsonDecode(body);
  }

  Future<void> _openExpandedMapResults() async {
    if (!_formKey.currentState!.validate()) return;
    final query =
        '${_keywordsController.text.trim()} ${_cityController.text.trim()}';
    final uri = Uri.https('www.google.com', '/maps/search/', {
      'api': '1',
      'query': query,
    });
    final opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!opened && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تعذر فتح خرائط Google على هذا الجهاز')),
      );
    }
  }

  Future<void> _showSmartWriter() async {
    final clientController = TextEditingController();
    final detailsController = TextEditingController(
      text: _keywordsController.text.trim(),
    );
    final priceController = TextEditingController();
    var documentType = 'تقرير متابعة اتصال';
    var contactMethod = 'واتساب';
    var contactResult = 'تم عرض السعر';
    var approvalLikelihood = 'جيدة جدًا';
    String? generated;
    try {
      generated = await showDialog<String>(
        context: context,
        builder: (dialogContext) => StatefulBuilder(
          builder: (context, setDialogState) => AlertDialog(
            backgroundColor: const Color(0xFF111B35),
            title: const Text(
              'الكاتب الذكي',
              style: TextStyle(color: Colors.white),
            ),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  DropdownButtonFormField<String>(
                    initialValue: documentType,
                    dropdownColor: const Color(0xFF172554),
                    style: const TextStyle(color: Colors.white),
                    decoration: _researchDecoration(
                      'نوع المستند',
                      Icons.description_outlined,
                    ),
                    items:
                        const ['تقرير متابعة اتصال', 'عرض سعر', 'تقدير أسعار']
                            .map(
                              (type) => DropdownMenuItem(
                                value: type,
                                child: Text(type),
                              ),
                            )
                            .toList(),
                    onChanged: (value) {
                      if (value != null) {
                        setDialogState(() => documentType = value);
                      }
                    },
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: clientController,
                    style: const TextStyle(color: Colors.white),
                    decoration: _researchDecoration(
                      'اسم الشركة أو المقاول',
                      Icons.business_outlined,
                    ),
                  ),
                  if (documentType == 'تقرير متابعة اتصال') ...[
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String>(
                      initialValue: contactMethod,
                      dropdownColor: const Color(0xFF172554),
                      style: const TextStyle(color: Colors.white),
                      decoration: _researchDecoration(
                        'طريقة التواصل',
                        Icons.call_outlined,
                      ),
                      items:
                          const [
                                'واتساب',
                                'مكالمة هاتفية',
                                'زيارة ميدانية',
                                'بريد إلكتروني',
                              ]
                              .map(
                                (value) => DropdownMenuItem(
                                  value: value,
                                  child: Text(value),
                                ),
                              )
                              .toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setDialogState(() => contactMethod = value);
                        }
                      },
                    ),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String>(
                      initialValue: contactResult,
                      dropdownColor: const Color(0xFF172554),
                      style: const TextStyle(color: Colors.white),
                      decoration: _researchDecoration(
                        'نتيجة التواصل',
                        Icons.fact_check_outlined,
                      ),
                      items:
                          const [
                                'تم عرض السعر',
                                'طلب تفاصيل إضافية',
                                'طلب معاينة',
                                'العرض تحت المراجعة',
                                'لم يتم الرد',
                                'اعتذر عن العرض',
                              ]
                              .map(
                                (value) => DropdownMenuItem(
                                  value: value,
                                  child: Text(value),
                                ),
                              )
                              .toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setDialogState(() => contactResult = value);
                        }
                      },
                    ),
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String>(
                      initialValue: approvalLikelihood,
                      dropdownColor: const Color(0xFF172554),
                      style: const TextStyle(color: Colors.white),
                      decoration: _researchDecoration(
                        'احتمالية الموافقة',
                        Icons.trending_up,
                      ),
                      items:
                          const [
                                'ممتازة',
                                'جيدة جدًا',
                                'جيدة',
                                'متوسطة',
                                'ضعيفة',
                              ]
                              .map(
                                (value) => DropdownMenuItem(
                                  value: value,
                                  child: Text(value),
                                ),
                              )
                              .toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setDialogState(() => approvalLikelihood = value);
                        }
                      },
                    ),
                  ],
                  const SizedBox(height: 12),
                  TextField(
                    controller: detailsController,
                    maxLines: 4,
                    style: const TextStyle(color: Colors.white),
                    decoration: _researchDecoration(
                      documentType == 'تقرير متابعة اتصال'
                          ? 'ملخص الحديث أو تفاصيل العمل'
                          : 'الخدمات أو تفاصيل العمل',
                      Icons.edit_note,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: priceController,
                    keyboardType: TextInputType.number,
                    style: const TextStyle(color: Colors.white),
                    decoration: _researchDecoration(
                      documentType == 'تقرير متابعة اتصال'
                          ? 'السعر المعروض بالريال (اختياري)'
                          : 'السعر الإجمالي بالريال (اختياري)',
                      Icons.payments_outlined,
                    ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('إلغاء'),
              ),
              FilledButton.icon(
                onPressed: () {
                  if (detailsController.text.trim().isEmpty) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('اكتب تفاصيل العمل أولًا')),
                    );
                    return;
                  }
                  if (documentType == 'تقرير متابعة اتصال' &&
                      clientController.text.trim().isEmpty) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text('اكتب اسم الشركة أو المقاول'),
                      ),
                    );
                    return;
                  }
                  Navigator.pop(
                    dialogContext,
                    _buildSmartDocument(
                      documentType,
                      clientController.text.trim(),
                      detailsController.text.trim(),
                      priceController.text.trim(),
                      contactMethod,
                      contactResult,
                      approvalLikelihood,
                    ),
                  );
                },
                icon: const Icon(Icons.auto_awesome),
                label: const Text('إنشاء'),
              ),
            ],
          ),
        ),
      );
    } finally {
      clientController.dispose();
      detailsController.dispose();
      priceController.dispose();
    }
    if (generated == null || !mounted) return;
    if (generated.startsWith('تقرير متابعة اتصال تجاري')) {
      generated = await _enhanceCommercialReportWithAi(generated);
      if (!mounted) return;
    }
    await showDialog<void>(
      context: context,
      builder: (resultContext) => AlertDialog(
        backgroundColor: const Color(0xFF111B35),
        title: const Text(
          'المستند الجاهز',
          style: TextStyle(color: Colors.white),
        ),
        content: SizedBox(
          width: double.maxFinite,
          child: SingleChildScrollView(
            child: SelectableText(
              generated!,
              textDirection: TextDirection.rtl,
              style: const TextStyle(color: Colors.white, height: 1.6),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(resultContext),
            child: const Text('إغلاق'),
          ),
          FilledButton.icon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: generated!));
              if (resultContext.mounted) Navigator.pop(resultContext);
              if (mounted) {
                ScaffoldMessenger.of(
                  context,
                ).showSnackBar(const SnackBar(content: Text('تم نسخ المستند')));
              }
            },
            icon: const Icon(Icons.copy),
            label: const Text('نسخ'),
          ),
        ],
      ),
    );
  }

  Future<String> _enhanceCommercialReportWithAi(String localReport) async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return localReport;
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      final result = await api.commercialReport({
        'نوع التقرير': 'متابعة اتصال تجاري',
        'المسودة الموثقة': localReport,
      });
      final text = result['text']?.toString().trim() ?? '';
      return text.isEmpty ? localReport : text;
    } catch (_) {
      return localReport;
    } finally {
      api.close();
    }
  }

  String _buildSmartDocument(
    String type,
    String client,
    String details,
    String price,
    String contactMethod,
    String contactResult,
    String approvalLikelihood,
  ) {
    final date = DateTime.now().toLocal().toString().split(' ').first;
    final recipient = client.isEmpty
        ? 'السادة/ العميل الكريم'
        : 'السادة/ $client';
    final amount = price.isEmpty
        ? 'يُحدد بعد المعاينة والاتفاق'
        : '$price ريال سعودي';
    if (type == 'تقرير متابعة اتصال') {
      final followUp = switch (approvalLikelihood) {
        'ممتازة' => 'المتابعة خلال 24 ساعة لإقفال الاتفاق',
        'جيدة جدًا' => 'المتابعة خلال يومي عمل',
        'جيدة' => 'المتابعة خلال 3 أيام',
        'متوسطة' => 'المتابعة خلال أسبوع مع توضيح مزايا العرض',
        _ => 'حفظ الفرصة للمتابعة المستقبلية',
      };
      final opportunityStatus = switch (approvalLikelihood) {
        'ممتازة' => 'فرصة ساخنة',
        'جيدة جدًا' => 'فرصة واعدة جدًا',
        'جيدة' => 'فرصة واعدة',
        'متوسطة' => 'تحتاج متابعة',
        _ => 'فرصة ضعيفة',
      };
      return '''تقرير متابعة اتصال تجاري
التاريخ: $date
الشركة/المقاول: ${client.isEmpty ? 'غير محدد' : client}
المدينة: ${_cityController.text.trim()}
طريقة التواصل: $contactMethod
نتيجة التواصل: $contactResult
السعر المعروض: $amount
احتمالية الموافقة: $approvalLikelihood
حالة الفرصة: $opportunityStatus

ملخص التواصل:
$details

الإجراء التالي:
â€¢ $followUp.
• توثيق أي ملاحظات أو تعديلات يطلبها العميل.
• تحديث حالة الفرصة بعد المتابعة.''';
    }
    if (type == 'تقدير أسعار') {
      return '''تقدير أسعار مبدئي
التاريخ: $date
$recipient

البنود المطلوبة:
$details

التقدير الإجمالي: $amount

ملاحظات:
• هذا التقدير مبدئي وليس فاتورة نهائية.
• السعر قابل للتغيير حسب المقاسات والكميات والخامات والموقع.
• لا يعتمد السعر إلا بعد المعاينة وموافقة الطرفين.''';
    }
    return '''عرض سعر
التاريخ: $date
إلى: $recipient

تحية طيبة،
يسرنا تقديم عرض السعر التالي:

نطاق العمل:
$details

الإجمالي: $amount

شروط العرض:
• مدة صلاحية العرض: 15 يومًا.
• يبدأ التنفيذ بعد اعتماد العرض والدفعة المتفق عليها.
• أي أعمال إضافية تُسعّر بشكل منفصل.
• السعر النهائي يعتمد على المقاسات والكميات المعتمدة.

وتفضلوا بقبول فائق الاحترام.''';
  }

  @override
  void dispose() {
    _keywordsController.dispose();
    _cityController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('موظف البحث التجاري'),
          actions: [
            PageRefreshButton(onRefresh: _loadSettings, confirmReload: true),
          ],
        ),
        body: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : Form(
                key: _formKey,
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: const Color(0xFF172554),
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(color: const Color(0xFF38BDF8)),
                      ),
                      child: const Row(
                        children: [
                          Icon(
                            Icons.manage_search,
                            color: Color(0xFF7DD3FC),
                            size: 34,
                          ),
                          SizedBox(width: 14),
                          Expanded(
                            child: Text(
                              'أنشئ مهمة بحث عن عملاء أو مقاولين أو أسعار، ثم راجع النتائج قبل استخدامها.',
                              style: TextStyle(
                                color: Colors.white70,
                                height: 1.5,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 18),
                    DropdownButtonFormField<String>(
                      initialValue: _researchType,
                      dropdownColor: const Color(0xFF172554),
                      style: const TextStyle(color: Colors.white),
                      decoration: _researchDecoration(
                        'نوع البحث',
                        Icons.category_outlined,
                      ),
                      items:
                          const [
                            'عملاء محتملون',
                            'مقاولون وموردون',
                            'أسعار السوق',
                          ].map((item) {
                            return DropdownMenuItem(
                              value: item,
                              child: Text(item),
                            );
                          }).toList(),
                      onChanged: (value) {
                        if (value == null) return;
                        setState(() => _researchType = value);
                      },
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _cityController,
                      validator: (value) =>
                          value == null || value.trim().isEmpty
                          ? 'اكتب المدينة أو المنطقة'
                          : null,
                      style: const TextStyle(color: Colors.white),
                      decoration: _researchDecoration(
                        'المدينة أو المنطقة',
                        Icons.location_city_outlined,
                      ).copyWith(hintText: 'مثال: الأحساء'),
                    ),
                    const SizedBox(height: 14),
                    TextFormField(
                      controller: _keywordsController,
                      maxLines: 3,
                      validator: (value) =>
                          value == null || value.trim().isEmpty
                          ? 'اكتب كلمات البحث'
                          : null,
                      style: const TextStyle(color: Colors.white),
                      decoration:
                          _researchDecoration(
                            'كلمات البحث',
                            Icons.key_outlined,
                          ).copyWith(
                            hintText:
                                'مثال: محلات جديدة، مقاول زجاج، أسعار مرايا',
                          ),
                    ),
                    const SizedBox(height: 20),
                    FilledButton.icon(
                      onPressed: _isSearching ? null : _runResearch,
                      icon: _isSearching
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                color: Colors.white,
                              ),
                            )
                          : const Icon(Icons.search),
                      label: Text(
                        _isSearching
                            ? 'جاري جلب البيانات...'
                            : 'ابدأ البحث الآن',
                      ),
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF0284C7),
                        padding: const EdgeInsets.symmetric(vertical: 16),
                      ),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: _isSearching ? null : _openExpandedMapResults,
                      icon: const Icon(Icons.map_outlined),
                      label: const Text('عرض مؤسسات أكثر في خرائط Google'),
                      style: OutlinedButton.styleFrom(
                        foregroundColor: const Color(0xFF7DD3FC),
                        side: const BorderSide(color: Color(0xFF38BDF8)),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                      ),
                    ),
                    const SizedBox(height: 10),
                    FilledButton.icon(
                      onPressed: _showSmartWriter,
                      icon: const Icon(Icons.auto_awesome),
                      label: const Text('إنشاء تقرير أو عرض سعر ذكي'),
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF7C3AED),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                      ),
                    ),
                    const SizedBox(height: 24),
                    Row(
                      children: [
                        const Expanded(
                          child: Text(
                            'الفرص المحفوظة',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                        Chip(
                          label: Text('${_savedLeads.length} فرصة'),
                          backgroundColor: const Color(0xFF0C4A6E),
                          labelStyle: const TextStyle(color: Color(0xFF7DD3FC)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    if (_savedLeads.isEmpty)
                      _researchMessage(
                        'احفظ أي نتيجة مناسبة لتصبح فرصة وتتابع المقاول والمقاسات والسعر.',
                        Icons.bookmark_add_outlined,
                      )
                    else
                      ..._savedLeads.map(_savedLeadCard),
                    const SizedBox(height: 24),
                    const Text(
                      'نتائج البحث',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 10),
                    if (_aiResearchText != null) ...[
                      CommercialResearchReport(text: _aiResearchText!),
                      const SizedBox(height: 12),
                    ],
                    if (_researchError != null)
                      _researchMessage(_researchError!, Icons.info_outline)
                    else if (_researchResults.isEmpty)
                      _researchMessage(
                        _isSearching
                            ? 'يبحث موظف AI عن البيانات الآن...'
                            : 'اكتب بيانات البحث وستظهر النتائج هنا داخل خدوم.',
                        Icons.travel_explore,
                      )
                    else
                      ..._researchResults.map(_researchResultCard),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _researchMessage(String message, IconData icon) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 28),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        children: [
          Icon(icon, color: const Color(0xFF7DD3FC), size: 42),
          const SizedBox(height: 12),
          Text(
            message,
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white70, height: 1.5),
          ),
        ],
      ),
    );
  }

  Widget _savedLeadCard(CommercialLead lead) {
    final pending = lead.priceDrafts
        .where((draft) => draft.status == PriceApprovalStatus.pending)
        .toList();
    final approved = lead.priceDrafts
        .where((draft) => draft.status == PriceApprovalStatus.approved)
        .toList();
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: pending.isNotEmpty
              ? const Color(0xFFF59E0B)
              : const Color(0xFF1E3A5F),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.apartment, color: Color(0xFF7DD3FC)),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  lead.projectName,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 16,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'حذف الفرصة',
                onPressed: () async {
                  final confirmed = await showDialog<bool>(
                    context: context,
                    builder: (dialogContext) => AlertDialog(
                      title: const Text('حذف الفرصة؟'),
                      content: Text(
                        'سيتم حذف ${lead.projectName} من الفرص المحفوظة.',
                      ),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.pop(dialogContext, false),
                          child: const Text('إلغاء'),
                        ),
                        FilledButton(
                          onPressed: () => Navigator.pop(dialogContext, true),
                          child: const Text('حذف'),
                        ),
                      ],
                    ),
                  );
                  if (confirmed == true) await _deleteCommercialLead(lead);
                },
                icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
              ),
            ],
          ),
          if (lead.contractor.isNotEmpty)
            Text(
              'المقاول: ${lead.contractor}',
              style: const TextStyle(color: Colors.white70),
            ),
          if (lead.location.isNotEmpty)
            Text(
              'الموقع: ${lead.location}',
              style: const TextStyle(color: Colors.white60),
            ),
          if (lead.facadeType.isNotEmpty)
            Text(
              'الخدمة: ${lead.facadeType}',
              style: const TextStyle(color: Colors.white70),
            ),
          if (lead.estimatedMeasurements.isNotEmpty)
            Text(
              'المقاسات التقديرية: ${lead.estimatedMeasurements}',
              style: const TextStyle(color: Color(0xFFFBBF24)),
            ),
          if (lead.contacts.isNotEmpty)
            Text(
              'واتساب: ${lead.contacts.first.whatsApp}',
              style: const TextStyle(color: Color(0xFF86EFAC)),
            ),
          if (pending.isNotEmpty) ...[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF3F2B12),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'السعر المقترح: ${pending.last.amount.toStringAsFixed(2)} ريال',
                    style: const TextStyle(
                      color: Color(0xFFFBBF24),
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  Text(
                    pending.last.details,
                    style: const TextStyle(color: Colors.white70),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Expanded(
                        child: FilledButton.icon(
                          onPressed: () =>
                              _reviewPrice(lead, pending.last, true),
                          icon: const Icon(Icons.check),
                          label: const Text('موافقة'),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: OutlinedButton(
                          onPressed: () =>
                              _reviewPrice(lead, pending.last, false),
                          child: const Text('رفض'),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
          if (approved.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              '✓ سعر معتمد: ${approved.last.amount.toStringAsFixed(2)} ريال',
              style: const TextStyle(
                color: Color(0xFF86EFAC),
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              FilledButton.icon(
                onPressed: () => _sendLeadIntroduction(lead),
                icon: const Icon(Icons.chat),
                label: const Text('تواصل عبر واتساب'),
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFF15803D),
                ),
              ),
              OutlinedButton.icon(
                onPressed: () => _createPriceApproval(lead),
                icon: const Icon(Icons.price_check),
                label: const Text('اقتراح سعر'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _researchResultCard(Map<String, dynamic> result) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFF1E3A5F)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const CircleAvatar(
            backgroundColor: Color(0xFF0C4A6E),
            child: Icon(Icons.storefront, color: Color(0xFF7DD3FC)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  result['name']?.toString() ?? 'نشاط تجاري',
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 16,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  result['address']?.toString() ?? '',
                  style: const TextStyle(color: Colors.white70, height: 1.4),
                ),
                const SizedBox(height: 7),
                Wrap(
                  spacing: 8,
                  children: [
                    Chip(label: Text(result['category']?.toString() ?? 'نشاط')),
                    const Chip(label: Text('المصدر: OpenStreetMap')),
                  ],
                ),
                const SizedBox(height: 10),
                FilledButton.icon(
                  onPressed: () => _saveResultAsLead(result),
                  icon: const Icon(Icons.bookmark_add_outlined),
                  label: const Text('حفظ كفرصة تجارية'),
                ),
                const SizedBox(height: 4),
                FilledButton.icon(
                  onPressed: () => showCommercialWhatsApp(
                    context,
                    projectName: result['name']?.toString() ?? 'نشاط تجاري',
                    phone: (result['whatsapp'] ?? result['phone'] ?? '')
                        .toString(),
                  ),
                  icon: const Icon(Icons.chat),
                  label: const Text('تواصل عبر واتساب'),
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF15803D),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  InputDecoration _researchDecoration(String label, IconData icon) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white70),
      hintStyle: const TextStyle(color: Colors.white38),
      prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
      filled: true,
      fillColor: const Color(0xFF172554),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFF38BDF8)),
      ),
    );
  }
}

class CallsEmployeePage extends StatefulWidget {
  const CallsEmployeePage({super.key});

  @override
  State<CallsEmployeePage> createState() => _CallsEmployeePageState();
}

class _CallsEmployeePageState extends State<CallsEmployeePage> {
  bool _isEnabled = false;
  bool _saveCallSummary = true;
  bool _askForName = true;
  bool _askForLocation = true;
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await BranchPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _isEnabled = prefs.getBool('calls_employee_enabled') ?? false;
      _saveCallSummary = prefs.getBool('calls_save_summary') ?? true;
      _askForName = prefs.getBool('calls_ask_name') ?? true;
      _askForLocation = prefs.getBool('calls_ask_location') ?? true;
      _isLoading = false;
    });
  }

  Future<void> _saveBool(String key, bool value) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setBool(key, value);
  }

  Future<void> _setEmployeeStatus(bool value) async {
    await _saveBool('calls_employee_enabled', value);
    if (!mounted) return;
    setState(() => _isEnabled = value);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          value ? 'تم تجهيز موظف الاتصالات للتشغيل' : 'تم إيقاف موظف الاتصالات',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('موظف الاتصالات'),
          actions: [PageRefreshButton(onRefresh: _loadSettings)],
        ),
        body: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Container(
                    padding: const EdgeInsets.all(18),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(18),
                      border: Border.all(
                        color: _isEnabled
                            ? const Color(0xFF22C55E)
                            : const Color(0xFFF59E0B),
                      ),
                    ),
                    child: Row(
                      children: [
                        Container(
                          width: 54,
                          height: 54,
                          decoration: BoxDecoration(
                            color:
                                (_isEnabled
                                        ? const Color(0xFF22C55E)
                                        : const Color(0xFFF59E0B))
                                    .withValues(alpha: 0.16),
                            shape: BoxShape.circle,
                          ),
                          child: Icon(
                            _isEnabled
                                ? Icons.phone_in_talk
                                : Icons.phone_disabled,
                            color: _isEnabled
                                ? const Color(0xFF4ADE80)
                                : const Color(0xFFFBBF24),
                          ),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _isEnabled
                                    ? 'موظف الاتصالات مفعّل'
                                    : 'موظف الاتصالات متوقف',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 16,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 3),
                              const Text(
                                'الاستقبال الفعلي للمكالمات يحتاج مزود اتصال سحابي وسيُربط لاحقًا.',
                                style: TextStyle(
                                  color: Colors.white60,
                                  height: 1.4,
                                ),
                              ),
                            ],
                          ),
                        ),
                        Switch(
                          value: _isEnabled,
                          activeThumbColor: const Color(0xFF38BDF8),
                          onChanged: _setEmployeeStatus,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 18),
                  const Text(
                    'بيانات يجمعها الموظف',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 10),
                  _callSettingTile(
                    icon: Icons.person_outline,
                    title: 'اسم العميل',
                    subtitle: 'يسأل المتصل عن اسمه قبل تسجيل الطلب',
                    value: _askForName,
                    onChanged: (value) async {
                      await _saveBool('calls_ask_name', value);
                      if (!mounted) return;
                      setState(() => _askForName = value);
                    },
                  ),
                  const SizedBox(height: 10),
                  _callSettingTile(
                    icon: Icons.location_on_outlined,
                    title: 'موقع العميل',
                    subtitle: 'يجمع المدينة والحي أو يطلب إرسال الموقع',
                    value: _askForLocation,
                    onChanged: (value) async {
                      await _saveBool('calls_ask_location', value);
                      if (!mounted) return;
                      setState(() => _askForLocation = value);
                    },
                  ),
                  const SizedBox(height: 10),
                  _callSettingTile(
                    icon: Icons.summarize_outlined,
                    title: 'حفظ ملخص المكالمة',
                    subtitle: 'ينشئ ملخصًا للخدمة والموعد والملاحظات',
                    value: _saveCallSummary,
                    onChanged: (value) async {
                      await _saveBool('calls_save_summary', value);
                      if (!mounted) return;
                      setState(() => _saveCallSummary = value);
                    },
                  ),
                  const SizedBox(height: 22),
                  const Text(
                    'سجل المكالمات',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 20,
                      vertical: 28,
                    ),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: const Column(
                      children: [
                        Icon(
                          Icons.phone_callback_outlined,
                          color: Color(0xFF7DD3FC),
                          size: 42,
                        ),
                        SizedBox(height: 12),
                        Text(
                          'لا توجد مكالمات مسجلة',
                          style: TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.bold,
                            fontSize: 17,
                          ),
                        ),
                        SizedBox(height: 6),
                        Text(
                          'سيظهر السجل بعد ربط مزود الاتصال بخادم خدوم.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.white54),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
      ),
    );
  }

  Widget _callSettingTile({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF172554),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          Icon(icon, color: const Color(0xFF7DD3FC)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 4),
                Text(subtitle, style: const TextStyle(color: Colors.white60)),
              ],
            ),
          ),
          Switch(
            value: value,
            activeThumbColor: const Color(0xFF38BDF8),
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }
}

class EmployeesPermissionsPage extends StatefulWidget {
  const EmployeesPermissionsPage({super.key});

  @override
  State<EmployeesPermissionsPage> createState() =>
      _EmployeesPermissionsPageState();
}

class _EmployeesPermissionsPageState extends State<EmployeesPermissionsPage> {
  final Future<BranchPreferences> _branchPrefs =
      BranchPreferences.getInstance();
  List<dynamic> _cloudEmployeesForQuota = [];
  static const _storageKey = 'business_employees';
  final List<Map<String, dynamic>> _employees = [];
  bool _loading = true;
  bool _canDeleteEmployees = true;

  static const Map<String, String> _permissionLabels = {
    'viewCustomers': 'العملاء — مشاهدة',
    'editCustomers': 'العملاء — إضافة وتعديل',
    'viewVehicles': 'المركبات — مشاهدة',
    'editVehicles': 'المركبات — إضافة وتعديل',
    'deleteVehicles': 'المركبات — حذف',
    'viewAppointments': 'المواعيد — مشاهدة',
    'manageAppointments': 'المواعيد — قبول وتعديل ورفض',
    'viewConversations': 'المحادثات — مشاهدة',
    'replyConversations': 'المحادثات — الرد على العملاء',
    'viewReports': 'التقارير — مشاهدة',
    'viewInvoices': 'الفواتير — مشاهدة',
    'editInvoices': 'الفواتير — إضافة وتعديل',
    'viewEmployees': 'الموظفون — مشاهدة',
    'manageEmployees': 'الموظفون — إضافة وتعديل وإيقاف',
    'deleteEmployees': 'الموظفون — حذف نهائي',
    'viewSettings': 'إعدادات المؤسسة — مشاهدة',
    'manageSettings': 'إعدادات المؤسسة — تعديل',
    'viewAuditLog': 'سجل العمليات — مشاهدة',
  };

  static const Map<String, List<String>> _permissionGroups = {
    'العملاء والمحادثات': [
      'viewCustomers',
      'editCustomers',
      'viewConversations',
      'replyConversations',
    ],
    'المواعيد والمركبات': [
      'viewAppointments',
      'manageAppointments',
      'viewVehicles',
      'editVehicles',
      'deleteVehicles',
    ],
    'الحسابات والتقارير': ['viewInvoices', 'editInvoices', 'viewReports'],
    'الموظفون والإدارة': [
      'viewEmployees',
      'manageEmployees',
      'deleteEmployees',
      'viewSettings',
      'manageSettings',
      'viewAuditLog',
    ],
  };

  static const Map<String, String> _requiredViewPermission = {
    'editCustomers': 'viewCustomers',
    'replyConversations': 'viewConversations',
    'manageAppointments': 'viewAppointments',
    'editVehicles': 'viewVehicles',
    'deleteVehicles': 'viewVehicles',
    'editInvoices': 'viewInvoices',
    'manageEmployees': 'viewEmployees',
    'deleteEmployees': 'viewEmployees',
    'manageSettings': 'viewSettings',
  };

  static const Map<String, Set<String>> _rolePresets = {
    'مدير': {
      'viewCustomers',
      'editCustomers',
      'viewVehicles',
      'editVehicles',
      'deleteVehicles',
      'viewAppointments',
      'manageAppointments',
      'viewConversations',
      'replyConversations',
      'viewReports',
      'viewInvoices',
      'editInvoices',
      'viewEmployees',
      'manageEmployees',
      'deleteEmployees',
      'viewSettings',
      'manageSettings',
      'viewAuditLog',
    },
    'مشرف': {
      'viewCustomers',
      'editCustomers',
      'viewVehicles',
      'editVehicles',
      'viewAppointments',
      'manageAppointments',
      'viewConversations',
      'replyConversations',
      'viewReports',
      'viewInvoices',
      'viewEmployees',
      'viewSettings',
    },
    'استقبال': {
      'viewCustomers',
      'editCustomers',
      'viewAppointments',
      'manageAppointments',
      'viewConversations',
      'replyConversations',
    },
    'محاسب': {'viewReports', 'viewInvoices', 'editInvoices'},
    'موظف': {'viewCustomers', 'viewVehicles', 'viewAppointments'},
  };

  @override
  void initState() {
    super.initState();
    _loadEmployees();
  }

  Future<KhdoomCloudApi?> _employeeApi(BranchPreferences prefs) async {
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return null;
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  Map<String, dynamic> _employeeFromCloud(Map<String, dynamic> item) {
    return {
      'id': item['id'].toString(),
      'synced': true,
      'name': item['name']?.toString() ?? '',
      'phone': item['phone']?.toString() ?? '',
      'username': item['username']?.toString() ?? '',
      'role': item['job_title']?.toString() ?? 'موظف',
      'active': item['active'] == true || item['active'] == 1,
      'permissions': Map<String, dynamic>.from(
        item['permissions'] as Map? ?? {},
      ),
    };
  }

  Future<void> _loadEmployees() async {
    final prefs = await _branchPrefs;
    _employees.clear();
    if (prefs.getString('session_user_type') == 'employee') {
      _canDeleteEmployees = false;
      final employeeId = prefs.getString('session_employee_id');
      final savedEmployees = prefs.getString(_storageKey);
      if (employeeId != null && savedEmployees != null) {
        for (final raw in jsonDecode(savedEmployees) as List) {
          final employee = Map<String, dynamic>.from(raw as Map);
          if (employee['id'].toString() == employeeId) {
            final permissions = Map<String, dynamic>.from(
              employee['permissions'] as Map? ?? {},
            );
            _canDeleteEmployees = permissions['deleteEmployees'] == true;
            break;
          }
        }
      }
    }
    final saved = prefs.getString(_storageKey);
    if (saved != null) {
      final decoded = jsonDecode(saved) as List<dynamic>;
      _employees.addAll(
        decoded.map((item) => Map<String, dynamic>.from(item as Map)),
      );
    }
    final api = await _employeeApi(prefs);
    if (api != null) {
      try {
        _cloudEmployeesForQuota = await api.employees(allBranches: true);
        await prefs.rememberEmployeeBranches(_cloudEmployeesForQuota);
        var cloudEmployees = _cloudEmployeesForQuota
            .where(
              (item) =>
                  BranchPreferences.employeeBranch(item as Map) ==
                  prefs.branchId,
            )
            .toList();
        if (_employees.any((employee) => employee['synced'] != true)) {
          const storage = FlutterSecureStorage();
          for (final employee in _employees) {
            if (employee['synced'] == true) continue;
            final oldId = employee['id'].toString();
            final password = await storage.read(
              key: 'employee_password_$oldId',
            );
            if (password == null || password.length < 8) continue;
            try {
              final created = await api.createEmployee({
                ...employee,
                'password': password,
              });
              final newId = created['id'].toString();
              employee['id'] = newId;
              employee['synced'] = true;
              employee['permissions'] = prefs.tagEmployee(
                employee,
              )['permissions'];
              await storage.write(
                key: 'employee_password_$newId',
                value: password,
              );
              if (newId != oldId) {
                await storage.delete(key: 'employee_password_$oldId');
              }
            } on CloudApiException {
              break;
            }
          }
          await _saveEmployees();
          _cloudEmployeesForQuota = await api.employees(allBranches: true);
          cloudEmployees = _cloudEmployeesForQuota
              .where(
                (item) =>
                    BranchPreferences.employeeBranch(item as Map) ==
                    prefs.branchId,
              )
              .toList();
        }
        {
          final unsynced = _employees
              .where((employee) => employee['synced'] != true)
              .toList();
          final localImages = {
            for (final employee in _employees)
              employee['id'].toString():
                  employee['imagePath']?.toString() ?? '',
          };
          _employees
            ..clear()
            ..addAll(
              cloudEmployees.map(
                (item) => {
                  ..._employeeFromCloud(Map<String, dynamic>.from(item as Map)),
                  'imagePath': localImages[item['id'].toString()] ?? '',
                },
              ),
            )
            ..addAll(
              unsynced.where(
                (employee) => !cloudEmployees.any(
                  (item) => item['id'].toString() == employee['id'].toString(),
                ),
              ),
            );
          await _saveEmployees();
        }
      } catch (_) {
        // تبقى القائمة المحلية متاحة عند انقطاع الخادم.
      } finally {
        api.close();
      }
    }
    if (!mounted) return;
    setState(() => _loading = false);
  }

  Future<void> _saveEmployees() async {
    final prefs = await _branchPrefs;
    await prefs.setString(_storageKey, jsonEncode(_employees));
  }

  Future<bool> _confirmSensitiveOperation({
    required String title,
    required String message,
  }) async {
    final controller = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: Text(title),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(message),
              const SizedBox(height: 14),
              const Text('اكتب كلمة «تأكيد» لإكمال العملية.'),
              const SizedBox(height: 8),
              TextField(
                controller: controller,
                autofocus: true,
                decoration: const InputDecoration(labelText: 'تأكيد العملية'),
                onChanged: (_) => setDialogState(() {}),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: {'تأكيد', 'تاكيد'}.contains(controller.text.trim())
                  ? () => Navigator.pop(dialogContext, true)
                  : null,
              child: const Text('متابعة'),
            ),
          ],
        ),
      ),
    );
    // انتظر انتهاء حركة إغلاق النافذة قبل التخلص من الحقل.
    await Future<void>.delayed(const Duration(milliseconds: 400));
    controller.dispose();
    return confirmed == true;
  }

  Future<void> _showEmployeeForm({int? index}) async {
    if (_loading) return;
    if (index == null) {
      final prefs = await _branchPrefs;
      final package = prefs.getString('subscription_package') ?? 'free';
      final limits = await PackageResourceLimits.load(prefs);
      final employeeLimit = limits.limit(package, 'employees');
      if (employeeLimit != null &&
          prefs.employeeUsage(_cloudEmployeesForQuota) >= employeeLimit) {
        if (!mounted) return;
        await showDialog<void>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('وصلت إلى حد الباقة'),
            content: Text(
              'وصلت إلى حد باقتك الحالي: $employeeLimit موظفين. يمكنك ترقية الباقة أو التواصل مع الإدارة.',
            ),
            actions: [
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('حسنًا'),
              ),
            ],
          ),
        );
        return;
      }
    }
    if (!mounted) return;
    final current = index == null ? null : _employees[index];
    final employeeId =
        current?['id']?.toString() ??
        DateTime.now().microsecondsSinceEpoch.toString();
    final nameController = TextEditingController(
      text: current?['name'] as String? ?? '',
    );
    final phoneController = TextEditingController(
      text: current?['phone'] as String? ?? '',
    );
    final usernameController = TextEditingController(
      text: current?['username'] as String? ?? '',
    );
    final passwordController = TextEditingController();
    String role = current?['role'] as String? ?? 'موظف';
    String employeeImage = current?['imagePath']?.toString() ?? '';
    bool active = current?['active'] as bool? ?? true;
    final currentPermissions = Map<String, dynamic>.from(
      current?['permissions'] as Map? ?? {},
    );
    const legacyViewKeys = <String, String>{
      'viewCustomers': 'customers',
      'viewVehicles': 'vehicles',
      'viewAppointments': 'appointments',
      'viewConversations': 'conversations',
      'viewReports': 'reports',
      'viewEmployees': 'employees',
      'viewSettings': 'settings',
    };
    final preset = _rolePresets[role] ?? const <String>{};
    final permissions = <String, bool>{
      for (final key in _permissionLabels.keys)
        key:
            currentPermissions[key] == true ||
            (legacyViewKeys[key] != null &&
                currentPermissions[legacyViewKeys[key]] == true) ||
            (current == null && preset.contains(key)),
    };

    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          backgroundColor: const Color(0xFF172554),
          title: Text(
            index == null ? 'إضافة موظف' : 'تعديل الموظف',
            style: const TextStyle(color: Colors.white),
          ),
          content: SizedBox(
            width: 420,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TextField(
                    controller: nameController,
                    style: const TextStyle(color: Colors.white),
                    decoration: _employeeInputDecoration(
                      'اسم الموظف',
                      Icons.person_outline,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: phoneController,
                    keyboardType: TextInputType.phone,
                    style: const TextStyle(color: Colors.white),
                    decoration: _employeeInputDecoration(
                      'رقم الجوال',
                      Icons.phone_outlined,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: usernameController,
                    style: const TextStyle(color: Colors.white),
                    autocorrect: false,
                    decoration: _employeeInputDecoration(
                      'اسم المستخدم للدخول',
                      Icons.account_circle_outlined,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: passwordController,
                    obscureText: true,
                    style: const TextStyle(color: Colors.white),
                    decoration: _employeeInputDecoration(
                      index == null
                          ? 'كلمة المرور'
                          : 'كلمة مرور جديدة (اتركها فارغة دون تغيير)',
                      Icons.password_outlined,
                    ),
                  ),
                  const SizedBox(height: 12),
                  DocumentImagePicker(
                    path: employeeImage,
                    onChanged: (value) =>
                        setDialogState(() => employeeImage = value),
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: role,
                    dropdownColor: const Color(0xFF172554),
                    style: const TextStyle(color: Colors.white),
                    decoration: _employeeInputDecoration(
                      'الدور الوظيفي',
                      Icons.badge_outlined,
                    ),
                    items: const [
                      DropdownMenuItem(value: 'مدير', child: Text('مدير')),
                      DropdownMenuItem(value: 'مشرف', child: Text('مشرف')),
                      DropdownMenuItem(
                        value: 'استقبال',
                        child: Text('استقبال'),
                      ),
                      DropdownMenuItem(value: 'موظف', child: Text('موظف')),
                      DropdownMenuItem(value: 'محاسب', child: Text('محاسب')),
                    ],
                    onChanged: (value) {
                      if (value != null) {
                        setDialogState(() {
                          role = value;
                          final selected =
                              _rolePresets[value] ?? const <String>{};
                          for (final key in permissions.keys) {
                            permissions[key] = selected.contains(key);
                          }
                        });
                      }
                    },
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'الصلاحيات الدقيقة',
                    style: TextStyle(
                      color: Color(0xFF7DD3FC),
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'فعّل فقط ما يحتاجه الموظف. التعديل أو الحذف يفعّل المشاهدة تلقائيًا.',
                    style: TextStyle(color: Colors.white60, fontSize: 12),
                  ),
                  const SizedBox(height: 4),
                  ..._permissionGroups.entries.map(
                    (group) => Container(
                      margin: const EdgeInsets.only(bottom: 10),
                      decoration: BoxDecoration(
                        color: const Color(0xFF0F1B3D),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFF285682)),
                      ),
                      child: ExpansionTile(
                        initiallyExpanded: true,
                        iconColor: const Color(0xFF7DD3FC),
                        collapsedIconColor: const Color(0xFF7DD3FC),
                        title: Text(
                          group.key,
                          style: const TextStyle(
                            color: Color(0xFF7DD3FC),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        children: group.value.map((key) {
                          return CheckboxListTile(
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 8,
                            ),
                            dense: true,
                            title: Text(
                              _permissionLabels[key] ?? key,
                              style: const TextStyle(color: Colors.white),
                            ),
                            value: permissions[key],
                            activeColor: const Color(0xFF38BDF8),
                            controlAffinity: ListTileControlAffinity.leading,
                            onChanged: (value) => setDialogState(() {
                              final enabled = value ?? false;
                              permissions[key] = enabled;
                              if (enabled) {
                                final required = _requiredViewPermission[key];
                                if (required != null) {
                                  permissions[required] = true;
                                }
                              }
                            }),
                          );
                        }).toList(),
                      ),
                    ),
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text(
                      'الحساب نشط',
                      style: TextStyle(color: Colors.white),
                    ),
                    value: active,
                    activeThumbColor: const Color(0xFF38BDF8),
                    onChanged: (value) => setDialogState(() => active = value),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: () {
                final name = nameController.text.trim();
                final username = usernameController.text.trim().toLowerCase();
                final password = passwordController.text;
                final usernameUsed = _employees.asMap().entries.any(
                  (entry) =>
                      entry.key != index &&
                      (entry.value['username'] as String? ?? '')
                              .toLowerCase() ==
                          username,
                );
                if (name.isEmpty || username.length < 3) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('اكتب اسم الموظف واسم مستخدم واضح'),
                    ),
                  );
                  return;
                }
                if (usernameUsed) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('اسم المستخدم مستخدم بالفعل')),
                  );
                  return;
                }
                if (index == null && password.length < 8) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text(
                        'كلمة المرور يجب أن تكون 8 خانات على الأقل',
                      ),
                    ),
                  );
                  return;
                }
                final savedPermissions = <String, bool>{
                  ...permissions,
                  'customers': permissions['viewCustomers'] == true,
                  'vehicles': permissions['viewVehicles'] == true,
                  'appointments': permissions['viewAppointments'] == true,
                  'conversations': permissions['viewConversations'] == true,
                  'reports': permissions['viewReports'] == true,
                  'employees': permissions['viewEmployees'] == true,
                  'settings': permissions['viewSettings'] == true,
                };
                Navigator.pop(dialogContext, {
                  'id': employeeId,
                  'imagePath': employeeImage,
                  'name': name,
                  'phone': phoneController.text.trim(),
                  'username': username,
                  'password': password,
                  'role': role,
                  'active': active,
                  'permissions': savedPermissions,
                });
              },
              child: const Text('حفظ'),
            ),
          ],
        ),
      ),
    );

    // Wait for the dialog reverse animation before disposing controllers.
    // Flutter still has inherited-widget dependents during that transition.
    await Future<void>.delayed(const Duration(milliseconds: 400));
    nameController.dispose();
    phoneController.dispose();
    usernameController.dispose();
    passwordController.dispose();
    if (result == null || !mounted) return;
    if (index != null) {
      final oldPermissions = jsonEncode(
        Map<String, dynamic>.from(current?['permissions'] as Map? ?? {}),
      );
      final newPermissions = jsonEncode(
        Map<String, dynamic>.from(result['permissions'] as Map? ?? {}),
      );
      final sensitiveChange =
          oldPermissions != newPermissions ||
          current?['active'] != result['active'] ||
          current?['role'] != result['role'];
      if (sensitiveChange) {
        final approved = await _confirmSensitiveOperation(
          title: 'تأكيد تغيير الصلاحيات',
          message: 'سيتم تغيير دور أو صلاحيات أو حالة حساب ${result['name']}.',
        );
        if (!approved || !mounted) return;
      }
    }
    final password = result['password']?.toString() ?? '';
    final prefs = await _branchPrefs;
    result['permissions'] = prefs.tagEmployee(result)['permissions'];
    if (prefs.allEmployees().any(
      (employee) =>
          employee['id'].toString() != employeeId &&
          employee['username']?.toString().trim().toLowerCase() ==
              result['username']?.toString().trim().toLowerCase(),
    )) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('اسم المستخدم مستخدم في أحد فروع المؤسسة'),
          ),
        );
      return;
    }
    final api = await _employeeApi(prefs);
    if (api != null) {
      try {
        if (index == null || current?['synced'] != true) {
          final created = await api.createEmployee(result);
          result['id'] = created['id'].toString();
        } else {
          await api.updateEmployee(current!['id'], result);
        }
        result['synced'] = true;
      } on CloudApiException catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
        return;
      } catch (_) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('تعذرت مزامنة الموظف مع الخادم، حاول مرة أخرى'),
          ),
        );
        return;
      } finally {
        api.close();
      }
    } else {
      result['synced'] = current?['synced'] == true;
    }
    result.remove('password');
    final savedEmployeeId = result['id'].toString();
    if (password.isNotEmpty) {
      const storage = FlutterSecureStorage();
      await storage.write(
        key: 'employee_password_$savedEmployeeId',
        value: password,
      );
      if (savedEmployeeId != employeeId) {
        await storage.delete(key: 'employee_password_$employeeId');
      }
    }
    if (!mounted) return;
    setState(() {
      if (index == null) {
        _employees.add(result);
      } else {
        _employees[index] = result;
      }
    });
    await _saveEmployees();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          api == null
              ? 'تم الحفظ على هذا الجهاز'
              : 'تم حفظ الموظف ومزامنته مع الخادم',
        ),
      ),
    );
  }

  InputDecoration _employeeInputDecoration(String label, IconData icon) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white70),
      prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
      filled: true,
      fillColor: const Color(0xFF0B1020),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide.none,
      ),
    );
  }

  Future<void> _deleteEmployee(int index) async {
    final employeeName = _employees[index]['name'] as String;
    final confirmed = await _confirmSensitiveOperation(
      title: 'حذف الموظف نهائيًا',
      message:
          'سيتم حذف $employeeName وفصل جلساته المفتوحة. لا يمكن التراجع عن العملية.',
    );
    if (confirmed != true || !mounted) return;
    final employee = _employees[index];
    final employeeId = employee['id']?.toString();
    final prefs = await _branchPrefs;
    final api = await _employeeApi(prefs);
    if (api != null && employee['synced'] == true && employeeId != null) {
      try {
        await api.deleteEmployee(employeeId);
      } on CloudApiException catch (error) {
        if (!mounted) return;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
        return;
      } catch (_) {
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر حذف الموظف من الخادم')),
        );
        return;
      } finally {
        api.close();
      }
    } else {
      api?.close();
    }
    if (employeeId != null) {
      const storage = FlutterSecureStorage();
      await storage.delete(key: 'employee_password_$employeeId');
    }
    _cloudEmployeesForQuota.removeWhere(
      (item) => item['id'].toString() == employeeId,
    );
    _employees.removeAt(index);
    if (mounted) setState(() {});
    await _saveEmployees();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('الموظفون والصلاحيات'),
          actions: [PageRefreshButton(onRefresh: _loadEmployees)],
        ),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: _showEmployeeForm,
          backgroundColor: const Color(0xFF38BDF8),
          foregroundColor: const Color(0xFF0B1020),
          icon: const Icon(Icons.person_add_alt_1),
          label: const Text('إضافة موظف'),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _employees.isEmpty
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.groups_outlined,
                        color: Color(0xFF7DD3FC),
                        size: 72,
                      ),
                      const SizedBox(height: 16),
                      const Text(
                        'لم تتم إضافة موظفين بعد',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 4),
                      const Text(
                        'أضف الموظف وحدد الصفحات والبيانات المسموح له بها.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.white60),
                      ),
                      const SizedBox(height: 18),
                      FilledButton.icon(
                        onPressed: _showEmployeeForm,
                        icon: const Icon(Icons.add),
                        label: const Text('إضافة أول موظف'),
                      ),
                    ],
                  ),
                ),
              )
            : ListView.separated(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
                itemCount: _employees.length,
                separatorBuilder: (_, _) => const SizedBox(height: 12),
                itemBuilder: (context, index) {
                  final employee = _employees[index];
                  final permissions = Map<String, dynamic>.from(
                    employee['permissions'] as Map,
                  );
                  final permissionCount = permissions.values
                      .where((value) => value == true)
                      .length;
                  final active = employee['active'] as bool? ?? true;
                  return Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0xFF172554),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(
                        color: active
                            ? const Color(0xFF38BDF8)
                            : Colors.white12,
                      ),
                    ),
                    child: Row(
                      children: [
                        if ((employee['imagePath']?.toString() ?? '')
                            .isNotEmpty)
                          DocumentImage(
                            path: employee['imagePath'].toString(),
                            title:
                                employee['name']?.toString() ?? 'صورة الموظف',
                            width: 50,
                            height: 50,
                          )
                        else
                          const CircleAvatar(
                            backgroundColor: Color(0xFF0C4A6E),
                            child: Icon(Icons.person, color: Color(0xFF7DD3FC)),
                          ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                employee['name'] as String,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.bold,
                                  fontSize: 17,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${employee['role']} • $permissionCount صلاحيات',
                                style: const TextStyle(color: Colors.white60),
                              ),
                              const SizedBox(height: 3),
                              Text(
                                'اسم الدخول: ${employee['username'] ?? 'غير محدد'}',
                                style: const TextStyle(
                                  color: Color(0xFF7DD3FC),
                                  fontSize: 12,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                active ? 'نشط' : 'موقوف',
                                style: TextStyle(
                                  color: active
                                      ? Colors.greenAccent
                                      : Colors.orangeAccent,
                                ),
                              ),
                            ],
                          ),
                        ),
                        PopupMenuButton<String>(
                          iconColor: Colors.white70,
                          onSelected: (value) {
                            if (value == 'edit') {
                              _showEmployeeForm(index: index);
                            }
                            if (value == 'delete') {
                              _deleteEmployee(index);
                            }
                          },
                          itemBuilder: (_) => [
                            const PopupMenuItem(
                              value: 'edit',
                              child: Text('تعديل'),
                            ),
                            if (_canDeleteEmployees)
                              const PopupMenuItem(
                                value: 'delete',
                                child: Text('حذف'),
                              ),
                          ],
                        ),
                      ],
                    ),
                  );
                },
              ),
      ),
    );
  }
}

class AiEmployeeCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;
  final bool locked;

  const AiEmployeeCard({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    this.onTap,
    this.locked = false,
  });

  @override
  Widget build(BuildContext context) {
    final accentColor = locked
        ? const Color(0xFFF59E0B)
        : const Color(0xFF38BDF8);
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(18),
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF172554),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: accentColor, width: 1.2),
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF111B35),
                borderRadius: BorderRadius.circular(14),
              ),
              child: Icon(icon, color: accentColor, size: 30),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: TextStyle(
                      color: locked ? Colors.white70 : Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    subtitle,
                    style: TextStyle(
                      color: locked ? const Color(0xFFFBBF24) : Colors.white70,
                      fontSize: 14,
                    ),
                  ),
                ],
              ),
            ),
            Icon(
              locked ? Icons.lock_outline : Icons.arrow_back_ios_new,
              color: accentColor,
              size: 20,
            ),
          ],
        ),
      ),
    );
  }
}

class ReceptionEmployeePage extends StatefulWidget {
  const ReceptionEmployeePage({super.key});

  @override
  State<ReceptionEmployeePage> createState() => _ReceptionEmployeePageState();
}

class _ReceptionEmployeePageState extends State<ReceptionEmployeePage> {
  bool _isEnabled = true;

  @override
  void initState() {
    super.initState();
    _loadStatus();
  }

  Future<void> _loadStatus() async {
    final prefs = await BranchPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _isEnabled = prefs.getBool('reception_employee_enabled') ?? true;
    });
  }

  Future<void> _setStatus(bool value) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setBool('reception_employee_enabled', value);
    if (!mounted) return;
    setState(() => _isEnabled = value);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          value ? 'تم تشغيل موظف الاستقبال' : 'تم إيقاف موظف الاستقبال',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          actions: [PageRefreshButton(onRefresh: _loadStatus)],
          backgroundColor: const Color(0xFF111B35),
          iconTheme: const IconThemeData(color: Colors.white),
          title: const Text(
            'موظف استقبال العملاء',
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
          ),
        ),
        body: Padding(
          padding: const EdgeInsets.all(16),
          child: ListView(
            children: [
              Container(
                padding: const EdgeInsets.all(18),
                decoration: BoxDecoration(
                  color: const Color(0xFF172554),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: const Color(0xFF38BDF8)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _isEnabled
                          ? 'موظف الاستقبال يعمل الآن 🤖'
                          : 'موظف الاستقبال متوقف مؤقتًا',
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'يستقبل العملاء، يفهم طلبهم، يجمع البيانات، ويرد على الاستفسارات الأولية.',
                      style: TextStyle(color: Colors.white70, fontSize: 15),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              _ReceptionOption(
                icon: Icons.chat_bubble_outline,
                title: 'المحادثات',
                subtitle: 'عرض محادثات العملاء',
                onTap: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const ReceptionConversationsPage(),
                    ),
                  );
                },
              ),
              const SizedBox(height: 12),
              _ReceptionOption(
                icon: Icons.quiz_outlined,
                title: 'تعليم الاستقبال: سؤال وجواب',
                subtitle: 'اكتب سؤال العميل وإجابته ثم احفظهما',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const QuestionAnswerTrainingPage(),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              _ReceptionOption(
                icon: Icons.settings,
                title: 'إعدادات الموظف',
                subtitle: 'طريقة الرد وبيانات المؤسسة',
                onTap: () {
                  Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const ReceptionSettingsPage(),
                    ),
                  );
                },
              ),
              const SizedBox(height: 12),
              _ReceptionOption(
                icon: Icons.smart_toy_outlined,
                title: 'محادثة الاستقبال',
                subtitle: 'محادثة محفوظة وطلبات تواصل تصل إلى المسؤول',
                onTap: _isEnabled
                    ? () {
                        Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const ReceptionAiTestPage(),
                          ),
                        );
                      }
                    : null,
              ),
              const SizedBox(height: 12),
              _ReceptionOption(
                icon: _isEnabled ? Icons.toggle_on : Icons.toggle_off,
                title: 'حالة الموظف',
                subtitle: _isEnabled
                    ? 'الموظف يعمل حاليًا'
                    : 'الموظف متوقف حاليًا',
                trailing: Switch(
                  value: _isEnabled,
                  activeThumbColor: const Color(0xFF38BDF8),
                  onChanged: _setStatus,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReceptionOption extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback? onTap;
  final Widget? trailing;

  const _ReceptionOption({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.onTap,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFF172554),
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Icon(icon, color: const Color(0xFF7DD3FC), size: 28),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 17,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 14,
                      ),
                    ),
                  ],
                ),
              ),
              trailing ??
                  const Icon(
                    Icons.arrow_back_ios_new,
                    color: Color(0xFF38BDF8),
                    size: 18,
                  ),
            ],
          ),
        ),
      ),
    );
  }
}

class ReceptionAiTestPage extends StatelessWidget {
  const ReceptionAiTestPage({super.key});
  @override
  Widget build(BuildContext context) => const ReceptionConversationPage();
}

class ReceptionConversationsPage extends StatelessWidget {
  const ReceptionConversationsPage({super.key});
  @override
  Widget build(BuildContext context) => const ReceptionConversationPage();
}

class WhatsAppConnectionPage extends StatelessWidget {
  const WhatsAppConnectionPage({super.key});
  @override
  Widget build(BuildContext context) => const WhatsAppWorkspace();
}

class ReceptionSettingsPage extends StatefulWidget {
  const ReceptionSettingsPage({super.key});

  @override
  State<ReceptionSettingsPage> createState() => _ReceptionSettingsPageState();
}

class _ReceptionSettingsPageState extends State<ReceptionSettingsPage> {
  final _formKey = GlobalKey<FormState>();
  final _businessNameController = TextEditingController();
  final _businessInfoController = TextEditingController();
  final _workingHoursController = TextEditingController();
  String _replyStyle = 'ودود ومختصر';
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final prefs = await BranchPreferences.getInstance();
    _businessNameController.text =
        prefs.getString('reception_business_name') ?? '';
    _businessInfoController.text =
        prefs.getString('reception_business_info') ?? '';
    _workingHoursController.text =
        prefs.getString('reception_working_hours') ?? '';
    if (!mounted) return;
    setState(() {
      _replyStyle = prefs.getString('reception_reply_style') ?? 'ودود ومختصر';
      _isLoading = false;
    });
  }

  Future<void> _saveSettings() async {
    if (!_formKey.currentState!.validate()) return;
    final prefs = await BranchPreferences.getInstance();
    await Future.wait([
      prefs.setString(
        'reception_business_name',
        _businessNameController.text.trim(),
      ),
      prefs.setString(
        'reception_business_info',
        _businessInfoController.text.trim(),
      ),
      prefs.setString(
        'reception_working_hours',
        _workingHoursController.text.trim(),
      ),
      prefs.setString('reception_reply_style', _replyStyle),
    ]);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('تم حفظ إعدادات موظف الاستقبال')),
    );
  }

  @override
  void dispose() {
    _businessNameController.dispose();
    _businessInfoController.dispose();
    _workingHoursController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('إعدادات موظف الاستقبال'),
          actions: [
            PageRefreshButton(onRefresh: _loadSettings, confirmReload: true),
          ],
        ),
        body: _isLoading
            ? const Center(child: CircularProgressIndicator())
            : Form(
                key: _formKey,
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    _settingsField(
                      controller: _businessNameController,
                      label: 'اسم المؤسسة',
                      icon: Icons.business,
                      validator: (value) =>
                          value == null || value.trim().isEmpty
                          ? 'اكتب اسم المؤسسة'
                          : null,
                    ),
                    const SizedBox(height: 14),
                    _settingsField(
                      controller: _businessInfoController,
                      label: 'نبذة عن النشاط والخدمات',
                      icon: Icons.info_outline,
                      maxLines: 4,
                    ),
                    const SizedBox(height: 14),
                    _settingsField(
                      controller: _workingHoursController,
                      label: 'ساعات العمل',
                      hint: 'مثال: الأحد إلى الخميس، 9 ص - 5 م',
                      icon: Icons.schedule,
                    ),
                    const SizedBox(height: 14),
                    DropdownButtonFormField<String>(
                      initialValue: _replyStyle,
                      dropdownColor: const Color(0xFF172554),
                      style: const TextStyle(color: Colors.white),
                      decoration: _fieldDecoration(
                        'أسلوب الرد',
                        Icons.forum_outlined,
                      ),
                      items: const ['ودود ومختصر', 'رسمي ومختصر', 'ودود ومفصل']
                          .map(
                            (style) => DropdownMenuItem(
                              value: style,
                              child: Text(style),
                            ),
                          )
                          .toList(),
                      onChanged: (value) =>
                          setState(() => _replyStyle = value!),
                    ),
                    const SizedBox(height: 24),
                    FilledButton.icon(
                      onPressed: _saveSettings,
                      icon: const Icon(Icons.save_outlined),
                      label: const Text('حفظ الإعدادات'),
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF0284C7),
                        padding: const EdgeInsets.symmetric(vertical: 16),
                      ),
                    ),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _settingsField({
    required TextEditingController controller,
    required String label,
    required IconData icon,
    String? hint,
    int maxLines = 1,
    String? Function(String?)? validator,
  }) {
    return TextFormField(
      controller: controller,
      maxLines: maxLines,
      validator: validator,
      style: const TextStyle(color: Colors.white),
      decoration: _fieldDecoration(label, icon).copyWith(hintText: hint),
    );
  }

  InputDecoration _fieldDecoration(String label, IconData icon) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: Colors.white70),
      hintStyle: const TextStyle(color: Colors.white38),
      prefixIcon: Icon(icon, color: const Color(0xFF7DD3FC)),
      filled: true,
      fillColor: const Color(0xFF172554),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: Color(0xFF38BDF8)),
      ),
    );
  }
}
