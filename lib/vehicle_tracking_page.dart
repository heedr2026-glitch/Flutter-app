import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

String trackingStatusLabel(String status) => switch (status) {
  'live' => 'موقع حديث خلال الدوام',
  'tracking' => 'التتبع يعمل خلال الدوام',
  'outside_schedule' => 'خارج الدوام — التتبع متوقف',
  'not_enabled' => 'بانتظار موافقة السائق وتفعيل جواله',
  'stopped' => 'أوقف السائق التتبع',
  'location_disabled' => 'الموقع غير مفعّل في الجوال',
  'permission_denied' => 'صلاحية الموقع غير متاحة',
  'last_location' => 'آخر موقع محفوظ — تحديث يدوي',
  'armed' => 'جاهز حسب جدول الدوام',
  'no_location' => 'لا يوجد موقع مسجل بعد',
  _ => 'غير متصل — آخر موقع قد يكون قديمًا',
};

class DriverTrackingNative {
  static const _channel = MethodChannel('khdoom/vehicle_tracking');
  static bool get supported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  static Future<void> start(Map<String, dynamic> config) async {
    if (!supported)
      throw StateError('تتبع جوال السائق متاح مبدئيًا على أندرويد');
    await _channel.invokeMethod<void>('start', config);
  }

  static Future<void> stop() async {
    if (supported) {
      try {
        await _channel.invokeMethod<void>('stop');
      } on MissingPluginException {}
    }
  }

  static Future<String> status() async => supported
      ? await _channel.invokeMethod<String>('status') ?? 'stopped'
      : 'not_enabled';
}

Future<KhdoomCloudApi> _trackingApi() async {
  final prefs = await BranchPreferences.getInstance();
  final token = await const FlutterSecureStorage().read(
    key: 'cloud_session_token',
  );
  if (token == null || token.isEmpty)
    throw const CloudApiException(401, 'سجّل الدخول بحساب المؤسسة أولًا');
  return KhdoomCloudApi(
    scope: prefs,
    baseUrl:
        prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
  )..token = token;
}

String _clock(num minute) =>
    '${(minute.toInt() ~/ 60).toString().padLeft(2, '0')}:${(minute.toInt() % 60).toString().padLeft(2, '0')}';
const _days = {
  1: 'الإثنين',
  2: 'الثلاثاء',
  3: 'الأربعاء',
  4: 'الخميس',
  5: 'الجمعة',
  6: 'السبت',
  7: 'الأحد',
};

class TrackingSchedulePage extends StatefulWidget {
  final String vehicleKey, vehicleName;
  const TrackingSchedulePage({
    super.key,
    required this.vehicleKey,
    required this.vehicleName,
  });
  @override
  State<TrackingSchedulePage> createState() => _TrackingSchedulePageState();
}

class _TrackingSchedulePageState extends State<TrackingSchedulePage> {
  KhdoomCloudApi? _api;
  List<Map<String, dynamic>> _drivers = [];
  int? _driver;
  Set<int> _weekdays = {7, 1, 2, 3, 4};
  TimeOfDay _start = const TimeOfDay(hour: 7, minute: 0),
      _end = const TimeOfDay(hour: 17, minute: 0);
  bool _enabled = true, _busy = true;
  String? _error;
  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _api?.close();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final api = await _trackingApi();
      if (!mounted) {
        api.close();
        return;
      }
      _api = api;
      final results = await Future.wait<dynamic>([
        api.trackingDrivers(),
        api.trackingSchedule(widget.vehicleKey),
      ]);
      final schedule = results[1] as Map<String, dynamic>;
      if (!mounted) return;
      _drivers = (results[0] as List)
          .map((e) => Map<String, dynamic>.from(e as Map))
          .toList();
      _driver = (schedule['driver_user_id'] as num?)?.toInt();
      if (!_drivers.any((d) => d['id'] == _driver)) _driver = null;
      if (schedule['weekdays'] is List)
        _weekdays = (schedule['weekdays'] as List)
            .map((e) => (e as num).toInt())
            .toSet();
      if (schedule['start_minute'] is num) {
        final m = (schedule['start_minute'] as num).toInt();
        _start = TimeOfDay(hour: m ~/ 60, minute: m % 60);
      }
      if (schedule['end_minute'] is num) {
        final m = (schedule['end_minute'] as num).toInt();
        _end = TimeOfDay(hour: m ~/ 60, minute: m % 60);
      }
      _enabled = schedule['enabled'] != 0;
    } catch (e) {
      _error = e.toString();
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _save() async {
    if (_driver == null || _weekdays.isEmpty) {
      setState(() => _error = 'اختر السائق وأيام الدوام');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await _api!.saveTrackingSchedule({
        'vehicleKey': widget.vehicleKey,
        'vehicleName': widget.vehicleName,
        'driverUserId': _driver,
        'weekdays': _weekdays.toList()..sort(),
        'startTime': _clock(_start.hour * 60 + _start.minute),
        'endTime': _clock(_end.hour * 60 + _end.minute),
        'enabled': _enabled,
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text(
              'تم حفظ الجدول؛ يلزم السائق قبول الجدول الجديد وتفعيل التتبع',
            ),
          ),
        );
        Navigator.pop(context);
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      appBar: AppBar(title: const Text('السائق وجدول الدوام')),
      body: _busy
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  widget.vehicleName,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 12),
                if (_error != null)
                  Text(_error!, style: const TextStyle(color: Colors.red)),
                DropdownButtonFormField<int>(
                  value: _driver,
                  decoration: const InputDecoration(
                    labelText: 'السائق — حساب المستخدم في المؤسسة',
                  ),
                  items: _drivers
                      .map(
                        (d) => DropdownMenuItem<int>(
                          value: (d['id'] as num).toInt(),
                          child: Text(d['name'].toString()),
                        ),
                      )
                      .toList(),
                  onChanged: (v) => setState(() => _driver = v),
                ),
                const SizedBox(height: 16),
                Wrap(
                  spacing: 8,
                  children: _days.entries
                      .map(
                        (d) => FilterChip(
                          label: Text(d.value),
                          selected: _weekdays.contains(d.key),
                          onSelected: (v) => setState(
                            () => v
                                ? _weekdays.add(d.key)
                                : _weekdays.remove(d.key),
                          ),
                        ),
                      )
                      .toList(),
                ),
                ListTile(
                  title: const Text('بداية الدوام — بتوقيت السعودية'),
                  trailing: Text(_clock(_start.hour * 60 + _start.minute)),
                  onTap: () async {
                    final t = await showTimePicker(
                      context: context,
                      initialTime: _start,
                    );
                    if (t != null && mounted) setState(() => _start = t);
                  },
                ),
                ListTile(
                  title: const Text('نهاية الدوام — بتوقيت السعودية'),
                  trailing: Text(_clock(_end.hour * 60 + _end.minute)),
                  onTap: () async {
                    final t = await showTimePicker(
                      context: context,
                      initialTime: _end,
                    );
                    if (t != null && mounted) setState(() => _end = t);
                  },
                ),
                SwitchListTile(
                  title: const Text('تفعيل الربط'),
                  value: _enabled,
                  onChanged: (v) => setState(() => _enabled = v),
                ),
                const Text(
                  'التتبع من جوال السائق داخل تطبيق خدووم. خارج الدوام لا يُجمع الموقع. تغيير السائق أو الجدول يحتاج موافقته من جديد. إذا كانت النهاية قبل البداية، يمتد الدوام لليوم التالي.',
                ),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: _api == null ? null : _save,
                  child: const Text('حفظ الجدول'),
                ),
              ],
            ),
    ),
  );
}

class DriverTrackingPage extends StatefulWidget {
  const DriverTrackingPage({super.key});
  @override
  State<DriverTrackingPage> createState() => _DriverTrackingPageState();
}

class _DriverTrackingPageState extends State<DriverTrackingPage> {
  KhdoomCloudApi? _api;
  Map<String, dynamic>? _assignment;
  String _status = 'stopped';
  String? _error;
  bool _busy = true, _accepted = false;
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    _load();
    _timer = Timer.periodic(
      const Duration(seconds: 15),
      (_) => _refreshStatus(),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    _api?.close();
    super.dispose();
  }

  Future<void> _refreshStatus() async {
    try {
      final s = await DriverTrackingNative.status();
      if (mounted) setState(() => _status = s);
    } catch (_) {}
  }

  Future<void> _load() async {
    try {
      final api = await _trackingApi();
      if (!mounted) {
        api.close();
        return;
      }
      _api?.close();
      _api = api;
      final a = await api.driverTrackingAssignment();
      if (mounted)
        setState(() => _assignment = a['status'] == 'not_assigned' ? null : a);
      await _refreshStatus();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _start() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (!DriverTrackingNative.supported)
        throw StateError('هذه الميزة متاحة مبدئيًا لجوال أندرويد');
      if (!await Geolocator.isLocationServiceEnabled())
        throw StateError('فعّل الموقع في إعدادات الجوال أولًا');
      var p = await Geolocator.checkPermission();
      if (p == LocationPermission.denied)
        p = await Geolocator.requestPermission();
      if (p == LocationPermission.denied ||
          p == LocationPermission.deniedForever)
        throw StateError('اسمح بالموقع الدقيق من إعدادات التطبيق');
      final notifications = FlutterLocalNotificationsPlugin()
          .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin
          >();
      if (await notifications?.requestNotificationsPermission() == false)
        throw StateError(
          'اسمح بإشعارات التطبيق لإظهار حالة التتبع وخيار إيقافه',
        );
      final api = _api!, a = _assignment!;
      await api.consentDriverTracking((a['revision'] as num).toInt());
      try {
        await DriverTrackingNative.start({
          'baseUrl': api.baseUri.toString(),
          'token': api.token,
          'vehicleKey': a['vehicle_key'],
          'revision': a['revision'],
          'branchId': a['branch_id'],
        });
      } catch (_) {
        await api.trackingHeartbeat('stopped');
        rethrow;
      }
      await _refreshStatus();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _stop() async {
    await DriverTrackingNative.stop();
    try {
      await _api?.trackingHeartbeat('stopped');
    } catch (_) {}
    await _refreshStatus();
  }

  @override
  Widget build(BuildContext context) {
    final a = _assignment;
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('تتبع دوامي'),
          actions: [
            IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
          ],
        ),
        body: _busy
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  if (_error != null)
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                  if (a == null)
                    const Text(
                      'لا توجد مركبة مرتبطة بحسابك. يربط مالك المؤسسة حسابك بالمركبة ويحدد جدول الدوام.',
                    )
                  else ...[
                    Text(
                      a['vehicle_name'].toString(),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    Text(
                      '${_clock(a['start_minute'] as num)} — ${_clock(a['end_minute'] as num)} بتوقيت السعودية',
                    ),
                    Text(
                      (a['weekdays'] as List)
                          .map((d) => _days[d] ?? '')
                          .join('، '),
                    ),
                    const SizedBox(height: 16),
                    Text(trackingStatusLabel(_status)),
                    const SizedBox(height: 16),
                    const Text(
                      'بعد موافقتك، يُرسل موقع جوالك لمالك المؤسسة أثناء الدوام فقط، حتى لو كانت الشاشة مقفلة. خارج الدوام يتوقف جمع الموقع. يبقى إشعار الخدمة ظاهرًا ويمكنك الإيقاف في أي وقت. تحتاج اتصالًا وموقعًا مفعّلًا؛ إذا أغلقت التطبيق بالقوة أو أوقف النظام الخدمة، افتح هذه الصفحة وفعّل التتبع مجددًا.',
                    ),
                    CheckboxListTile(
                      value: _accepted,
                      onChanged: (v) => setState(() => _accepted = v ?? false),
                      title: const Text(
                        'أوافق على تتبع جوالي للمركبة ضمن الأيام والأوقات الموضحة',
                      ),
                    ),
                    FilledButton(
                      onPressed: _accepted ? _start : null,
                      child: const Text('الموافقة وتفعيل التتبع'),
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton(
                      onPressed: _stop,
                      child: const Text('إيقاف التتبع'),
                    ),
                  ],
                ],
              ),
      ),
    );
  }
}
