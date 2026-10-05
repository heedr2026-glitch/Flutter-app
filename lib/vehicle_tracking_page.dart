import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:qr_flutter/qr_flutter.dart';
import 'package:url_launcher/url_launcher.dart';

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
/// عرض الوقت بنظام 12 ساعة (ص/م)؛ الإرسال للخادم يبقى 24 ساعة عبر [_clock].
String _clock12(num minute) {
  final m = minute.toInt();
  final hour24 = (m ~/ 60) % 24;
  final hour = hour24 % 12 == 0 ? 12 : hour24 % 12;
  return '$hour:${(m % 60).toString().padLeft(2, '0')} ${hour24 < 12 ? 'ص' : 'م'}';
}

/// وقت آخر موقع بتوقيت الجوال وبنظام 12 ساعة، مثل: 2026-10-02 3:05 م.
String trackingTimeLabel(Object? iso) {
  final parsed = DateTime.tryParse('${iso ?? ''}');
  if (parsed == null) return 'غير متاح';
  final t = parsed.toLocal();
  final date =
      '${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')}';
  return '$date ${_clock12(t.hour * 60 + t.minute)}';
}

Widget _twelveHourPicker(BuildContext context, Widget? child) => MediaQuery(
  data: MediaQuery.of(context).copyWith(alwaysUse24HourFormat: false),
  child: child ?? const SizedBox.shrink(),
);

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
  String _linkUrl = '', _linkCode = '', _driverName = '';
  bool _linked = false;
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
      final schedule = await api.trackingSchedule(widget.vehicleKey);
      if (!mounted) return;
      _applyLink(schedule);
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

  void _applyLink(Map<String, dynamic> schedule) {
    _linkUrl = (schedule['linkUrl'] ?? '').toString();
    _linkCode = (schedule['linkCode'] ?? '').toString();
    _driverName = (schedule['driverName'] ?? '').toString();
    _linked = schedule['linked'] == true;
  }

  Future<void> _linkAction(bool rotate) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (c) => Directionality(
        textDirection: TextDirection.rtl,
        child: AlertDialog(
          title: Text(rotate ? 'باركود جديد؟' : 'إلغاء ربط السائق؟'),
          content: Text(
            rotate
                ? 'يُلغى الباركود الحالي وينفصل جوال السائق المرتبط. يلزم السائق تصوير الباركود الجديد.'
                : 'ينفصل جوال السائق ويتوقف التتبع حتى يصوّر الباركود من جديد.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('تراجع'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('تأكيد'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final schedule = rotate
          ? await _api!.rotateTrackingLink(widget.vehicleKey)
          : await _api!.unlinkTrackingDriver(widget.vehicleKey);
      _applyLink(schedule);
    } catch (e) {
      _error = e.toString();
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _save() async {
    if (_weekdays.isEmpty) {
      setState(() => _error = 'اختر أيام الدوام');
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
        'weekdays': _weekdays.toList()..sort(),
        'startTime': _clock(_start.hour * 60 + _start.minute),
        'endTime': _clock(_end.hour * 60 + _end.minute),
        'enabled': _enabled,
      });
      final schedule = await _api!.trackingSchedule(widget.vehicleKey);
      _applyLink(schedule);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('تم حفظ الدوام. باركود المركبة جاهز أسفل الصفحة'),
          ),
        );
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
      appBar: AppBar(title: const Text('الدوام وباركود السائق')),
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
                  trailing: Text(_clock12(_start.hour * 60 + _start.minute)),
                  onTap: () async {
                    final t = await showTimePicker(
                      context: context,
                      initialTime: _start,
                      builder: _twelveHourPicker,
                    );
                    if (t != null && mounted) setState(() => _start = t);
                  },
                ),
                ListTile(
                  title: const Text('نهاية الدوام — بتوقيت السعودية'),
                  trailing: Text(_clock12(_end.hour * 60 + _end.minute)),
                  onTap: () async {
                    final t = await showTimePicker(
                      context: context,
                      initialTime: _end,
                      builder: _twelveHourPicker,
                    );
                    if (t != null && mounted) setState(() => _end = t);
                  },
                ),
                SwitchListTile(
                  title: const Text('تفعيل التتبع لهذه المركبة'),
                  value: _enabled,
                  onChanged: (v) => setState(() => _enabled = v),
                ),
                const Text(
                  'التتبع من جوال السائق داخل تطبيق خدووم، بدون حساب: يضغط «أنا سائق» ويصوّر الباركود ويوافق. خارج الدوام لا يُجمع الموقع. تعديل الدوام يصل لجوال السائق تلقائيًا. إذا كانت النهاية قبل البداية، يمتد الدوام لليوم التالي.',
                ),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: _api == null ? null : _save,
                  child: const Text('حفظ الدوام'),
                ),
                if (_linkUrl.isNotEmpty) ...[
                  const SizedBox(height: 24),
                  const Divider(),
                  Text(
                    'باركود المركبة',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 12),
                  Center(
                    child: Container(
                      color: Colors.white,
                      padding: const EdgeInsets.all(12),
                      child: QrImageView(
                        data: _linkUrl,
                        version: QrVersions.auto,
                        size: 220,
                        backgroundColor: Colors.white,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Center(
                    child: SelectableText(
                      _linkCode,
                      style: const TextStyle(
                        fontSize: 22,
                        letterSpacing: 4,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _linked
                        ? 'السائق المرتبط: $_driverName'
                        : 'لا يوجد سائق مرتبط بعد',
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'اجعل الباركود داخل المركبة فقط؛ من يصوّره يربط جواله بالمركبة ويفصل السائق الحالي.',
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 12),
                  Wrap(
                    alignment: WrapAlignment.center,
                    spacing: 8,
                    children: [
                      if (_linked)
                        OutlinedButton(
                          onPressed: () => _linkAction(false),
                          child: const Text('إلغاء ربط السائق'),
                        ),
                      OutlinedButton(
                        onPressed: () => _linkAction(true),
                        child: const Text('باركود جديد'),
                      ),
                    ],
                  ),
                ],
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
                      '${_clock12(a['start_minute'] as num)} — ${_clock12(a['end_minute'] as num)} بتوقيت السعودية',
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
                      'بعد موافقتك، يُرسل موقع جوالك لمالك المؤسسة أثناء الدوام فقط، حتى لو كانت الشاشة مقفلة. خارج الدوام يتوقف جمع الموقع. يبقى إشعار الخدمة ظاهرًا ويمكنك الإيقاف في أي وقت. إذا انقطع الإنترنت يُحفظ مسارك خلال الدوام في جوالك ويُرسل عند رجوع الاتصال. تحتاج موقعًا مفعّلًا؛ إذا أغلقت التطبيق بالقوة أو أوقف النظام الخدمة، افتح هذه الصفحة وفعّل التتبع مجددًا.',
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

/// المركبات المرتبطة بالتتبع؛ مشاهدة فقط لمن منحه صاحب المؤسسة الصلاحية.
class TrackedVehiclesPage extends StatefulWidget {
  const TrackedVehiclesPage({super.key});
  @override
  State<TrackedVehiclesPage> createState() => _TrackedVehiclesPageState();
}

class _TrackedVehiclesPageState extends State<TrackedVehiclesPage> {
  List<Map<String, dynamic>> _items = [];
  String? _error;
  bool _busy = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    KhdoomCloudApi? api;
    try {
      api = await _trackingApi();
      final rows = await api.trackedVehicles();
      _items = [for (final row in rows) Map<String, dynamic>.from(row as Map)];
    } catch (e) {
      _error = e.toString();
      _items = [];
    } finally {
      api?.close();
    }
    if (mounted) setState(() => _busy = false);
  }

  void _say(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _showLast(Map<String, dynamic> item) async {
    KhdoomCloudApi? api;
    try {
      api = await _trackingApi();
      final location = await api.vehicleTracking(
        (item['vehicleKey'] ?? '').toString(),
      );
      final latitude = (location['latitude'] as num?)?.toDouble();
      final longitude = (location['longitude'] as num?)?.toDouble();
      final status = (location['status'] ?? 'no_location').toString();
      if (latitude == null || longitude == null) {
        _say(trackingStatusLabel(status));
        return;
      }
      if (!mounted) return;
      final map = Uri.parse(
        'https://www.google.com/maps/search/?api=1&query=$latitude,$longitude',
      );
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            title: Text(trackingStatusLabel(status)),
            content: Text(
              'آخر موقع مسجل: ${trackingTimeLabel(location['recorded_at'])}\nالموقع يمثل جوال السائق؛ قد يختلف عن المركبة إذا ابتعد عنها.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('إغلاق'),
              ),
              FilledButton(
                onPressed: () async {
                  var opened = false;
                  try {
                    opened = await launchUrl(
                      map,
                      mode: LaunchMode.externalApplication,
                    );
                  } catch (_) {}
                  if (!opened) {
                    try {
                      opened = await launchUrl(map);
                    } catch (_) {}
                  }
                  if (!opened) {
                    _say('تعذر فتح الخريطة. الإحداثيات: $latitude, $longitude');
                  }
                },
                child: const Text('فتح الخريطة'),
              ),
            ],
          ),
        ),
      );
    } catch (e) {
      _say('تعذر عرض الموقع: $e');
    } finally {
      api?.close();
    }
  }

  Widget _card(Map<String, dynamic> item) {
    final name = (item['vehicleName'] ?? 'مركبة').toString();
    final driver = (item['driverName'] ?? '').toString();
    final lastAt = item['lastAt'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(name, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            if (driver.isNotEmpty) Text('السائق: $driver'),
            Text(
              trackingStatusLabel((item['status'] ?? 'offline').toString()),
            ),
            Text(
              lastAt == null
                  ? 'لا يوجد موقع مسجل بعد'
                  : 'آخر موقع: ${trackingTimeLabel(lastAt)}',
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: () => _showLast(item),
                  icon: const Icon(Icons.map_outlined),
                  label: const Text('عرض آخر موقع'),
                ),
                OutlinedButton.icon(
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => VehicleRoutePage(
                        vehicleKey: (item['vehicleKey'] ?? '').toString(),
                        vehicleName: name,
                      ),
                    ),
                  ),
                  icon: const Icon(Icons.route_outlined),
                  label: const Text('مسار المركبة'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      appBar: AppBar(
        title: const Text('تتبع المركبات'),
        actions: [
          IconButton(
            onPressed: _busy ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _busy
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _error!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.red),
                ),
              ),
            )
          : _items.isEmpty
          ? const Center(
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'لا توجد مركبات مرتبطة بالتتبع بعد. يربط صاحب المؤسسة المركبة بجوال السائق من صفحة المركبات.',
                  textAlign: TextAlign.center,
                ),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    'مشاهدة فقط. فتح مسار المركبة يُسجَّل في سجل العمليات.',
                    style: TextStyle(fontSize: 12),
                  ),
                ),
                for (final item in _items) _card(item),
              ],
            ),
    ),
  );
}

/// مسار المركبة ليوم واحد على خريطة داخل التطبيق؛ للمالك ولمن منحه صلاحية المشاهدة.
class VehicleRoutePage extends StatefulWidget {
  final String vehicleKey, vehicleName;
  const VehicleRoutePage({
    super.key,
    required this.vehicleKey,
    required this.vehicleName,
  });
  @override
  State<VehicleRoutePage> createState() => _VehicleRoutePageState();
}

class _VehicleRoutePageState extends State<VehicleRoutePage> {
  KhdoomCloudApi? _api;
  DateTime _day = DateTime.now();
  List<LatLng> _points = [];
  Map<String, dynamic> _summary = {};
  String? _error;
  bool _busy = true;

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

  String get _dayText =>
      '${_day.year}-${_day.month.toString().padLeft(2, '0')}-${_day.day.toString().padLeft(2, '0')}';

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      _api ??= await _trackingApi();
      final data = await _api!.vehicleRoute(widget.vehicleKey, _dayText);
      final points = <LatLng>[];
      for (final item in (data['points'] as List? ?? const [])) {
        final point = Map<String, dynamic>.from(item as Map);
        final latitude = (point['latitude'] as num?)?.toDouble();
        final longitude = (point['longitude'] as num?)?.toDouble();
        if (latitude != null && longitude != null) {
          points.add(LatLng(latitude, longitude));
        }
      }
      _points = points;
      _summary = data;
    } catch (e) {
      _error = e.toString();
      _points = [];
      _summary = {};
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _pickDay() async {
    final now = DateTime.now();
    final chosen = await showDatePicker(
      context: context,
      initialDate: _day,
      firstDate: now.subtract(const Duration(days: 30)),
      lastDate: now,
    );
    if (chosen == null || !mounted) return;
    _day = chosen;
    await _load();
  }

  String _distanceText() {
    final meters = (_summary['distanceMeters'] as num?)?.toDouble() ?? 0;
    if (meters < 1000) return '${meters.round()} م';
    return '${(meters / 1000).toStringAsFixed(1)} كم';
  }

  Widget _map() {
    final first = _points.first, last = _points.last;
    var south = first.latitude, north = first.latitude;
    var west = first.longitude, east = first.longitude;
    for (final point in _points) {
      if (point.latitude < south) south = point.latitude;
      if (point.latitude > north) north = point.latitude;
      if (point.longitude < west) west = point.longitude;
      if (point.longitude > east) east = point.longitude;
    }
    // مركبة لم تتحرك تقريبًا: نعرضها بتكبير ثابت بدل حدود صفرية.
    final still = (north - south).abs() < 0.0005 && (east - west).abs() < 0.0005;
    return Stack(
      children: [
        FlutterMap(
          key: ValueKey('$_dayText-${_points.length}'),
          options: still
              ? MapOptions(initialCenter: last, initialZoom: 16)
              : MapOptions(
                  initialCameraFit: CameraFit.bounds(
                    bounds: LatLngBounds(
                      LatLng(south, west),
                      LatLng(north, east),
                    ),
                    padding: const EdgeInsets.all(48),
                  ),
                ),
          children: [
            TileLayer(
              urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
              userAgentPackageName: 'com.khdoom.app',
            ),
            PolylineLayer(
              polylines: [
                Polyline(
                  points: _points,
                  strokeWidth: 4,
                  color: const Color(0xFF2563EB),
                ),
              ],
            ),
            MarkerLayer(
              markers: [
                Marker(
                  point: first,
                  width: 36,
                  height: 36,
                  child: const Icon(
                    Icons.play_circle_fill,
                    color: Color(0xFF16A34A),
                    size: 30,
                  ),
                ),
                Marker(
                  point: last,
                  width: 40,
                  height: 40,
                  child: const Icon(
                    Icons.location_on,
                    color: Color(0xFFDC2626),
                    size: 38,
                  ),
                ),
              ],
            ),
          ],
        ),
        const Positioned(
          left: 6,
          bottom: 4,
          child: Text(
            '© OpenStreetMap',
            style: TextStyle(fontSize: 11, color: Colors.black87),
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final driver = (_summary['driverName'] ?? '').toString();
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('مسار المركبة'),
          actions: [
            IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
          ],
        ),
        body: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.vehicleName,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  OutlinedButton.icon(
                    onPressed: _busy ? null : _pickDay,
                    icon: const Icon(Icons.calendar_today, size: 18),
                    label: Text(_dayText),
                  ),
                ],
              ),
            ),
            Expanded(
              child: _busy
                  ? const Center(child: CircularProgressIndicator())
                  : _error != null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: Text(
                          _error!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.red),
                        ),
                      ),
                    )
                  : _points.isEmpty
                  ? const Center(
                      child: Padding(
                        padding: EdgeInsets.all(24),
                        child: Text(
                          'لا توجد مواقع مسجلة في هذا اليوم. المسار يُسجَّل خلال الدوام فقط ومن جوال السائق المرتبط.',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    )
                  : _map(),
            ),
            if (!_busy && _error == null && _points.isNotEmpty)
              Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (driver.isNotEmpty) Text('السائق: $driver'),
                    Text('أول موقع: ${trackingTimeLabel(_summary['firstAt'])}'),
                    Text('آخر موقع: ${trackingTimeLabel(_summary['lastAt'])}'),
                    Text('المسافة التقريبية: ${_distanceText()}'),
                    const SizedBox(height: 4),
                    const Text(
                      'الأخضر بداية المسار والأحمر آخر موقع. تُحفظ المسارات 30 يومًا.',
                      style: TextStyle(fontSize: 12),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
