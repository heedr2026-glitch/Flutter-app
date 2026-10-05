import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:mobile_scanner/mobile_scanner.dart';

import 'vehicle_tracking_page.dart';

/// ربط جوال السائق بالمركبة عبر الباركود، بدون حساب ولا كلمة مرور.
const _driverApiBase = 'https://khdoom-api.onrender.com';
const _driverLinkInfoKey = 'driver_link_info';
const _driverLinkTokenKey = 'driver_link_token';

/// عرض الوقت بنظام 12 ساعة (ص/م).
String _driverClock(num minute) {
  final m = minute.toInt();
  final hour24 = (m ~/ 60) % 24;
  final hour = hour24 % 12 == 0 ? 12 : hour24 % 12;
  return '$hour:${(m % 60).toString().padLeft(2, '0')} ${hour24 < 12 ? 'ص' : 'م'}';
}

const _driverDays = {
  1: 'الإثنين',
  2: 'الثلاثاء',
  3: 'الأربعاء',
  4: 'الخميس',
  5: 'الجمعة',
  6: 'السبت',
  7: 'الأحد',
};

String _driverScheduleText(Map<String, dynamic> info) {
  final days = info['weekdays'] is List
      ? (info['weekdays'] as List)
            .map((d) => _driverDays[(d as num).toInt()] ?? '')
            .join('، ')
      : '';
  final start = info['start_minute'] is num
      ? _driverClock(info['start_minute'] as num)
      : '';
  final end = info['end_minute'] is num
      ? _driverClock(info['end_minute'] as num)
      : '';
  return '$days\nمن $start إلى $end بتوقيت السعودية';
}

Future<Map<String, dynamic>> _driverPost(
  String path,
  Map<String, dynamic> body, {
  String? token,
}) async {
  final headers = <String, String>{'Content-Type': 'application/json'};
  if (token != null) headers['Authorization'] = 'Bearer $token';
  final response = await http
      .post(
        Uri.parse('$_driverApiBase$path'),
        headers: headers,
        body: jsonEncode(body),
      )
      .timeout(const Duration(seconds: 70));
  final text = utf8.decode(response.bodyBytes);
  final decoded = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
  if (response.statusCode < 200 || response.statusCode >= 300) {
    final message = decoded is Map ? decoded['error']?.toString() : null;
    throw StateError(message ?? 'تعذر الاتصال بالخادم');
  }
  return Map<String, dynamic>.from(decoded as Map);
}

class DriverLinkPage extends StatefulWidget {
  const DriverLinkPage({super.key});
  @override
  State<DriverLinkPage> createState() => _DriverLinkPageState();
}

class _DriverLinkPageState extends State<DriverLinkPage> {
  final _storage = const FlutterSecureStorage();
  final _codeController = TextEditingController();
  final _nameController = TextEditingController();
  Map<String, dynamic>? _linked;
  Map<String, dynamic>? _preview;
  String _code = '';
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
    _codeController.dispose();
    _nameController.dispose();
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
      final saved = await _storage.read(key: _driverLinkInfoKey);
      if (saved != null && saved.isNotEmpty) {
        _linked = Map<String, dynamic>.from(jsonDecode(saved) as Map);
      }
      await _refreshStatus();
    } catch (e) {
      _error = e.toString();
    }
    if (mounted) setState(() => _busy = false);
  }

  String _message(Object error) =>
      error is StateError ? error.message : error.toString();

  Future<void> _scan() async {
    final value = await Navigator.push<String>(
      context,
      MaterialPageRoute(builder: (_) => const _DriverScanPage()),
    );
    if (value == null || value.isEmpty || !mounted) return;
    _codeController.text = value;
    await _lookup();
  }

  Future<void> _lookup() async {
    final code = _codeController.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'صوّر باركود المركبة أو اكتب الكود');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final preview = await _driverPost('/api/driver-link/preview', {
        'code': code,
      });
      if (mounted) {
        setState(() {
          _preview = preview;
          _code = code;
          _accepted = false;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = _message(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _accept() async {
    final name = _nameController.text.trim();
    if (name.length < 2) {
      setState(() => _error = 'اكتب اسمك أولًا');
      return;
    }
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
      final claim = await _driverPost('/api/driver-link/claim', {
        'code': _code,
        'driverName': name,
        'accepted': true,
      });
      final token = claim['token'].toString();
      try {
        await DriverTrackingNative.start({
          'baseUrl': _driverApiBase,
          'token': token,
          'vehicleKey': claim['vehicleKey'],
          'revision': claim['revision'],
          'branchId': claim['branchId'],
        });
      } catch (_) {
        try {
          await _driverPost('/api/vehicle-tracking/heartbeat', {
            'status': 'stopped',
          }, token: token);
        } catch (_) {}
        rethrow;
      }
      final info = <String, dynamic>{
        'vehicleName': claim['vehicleName'],
        'organizationName': claim['organizationName'],
        'weekdays': claim['weekdays'],
        'start_minute': claim['start_minute'],
        'end_minute': claim['end_minute'],
        'driverName': name,
      };
      await _storage.write(key: _driverLinkTokenKey, value: token);
      await _storage.write(key: _driverLinkInfoKey, value: jsonEncode(info));
      if (mounted) {
        setState(() {
          _linked = info;
          _preview = null;
        });
      }
      await _refreshStatus();
    } catch (e) {
      if (mounted) setState(() => _error = _message(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _stop() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await DriverTrackingNative.stop();
      final token = await _storage.read(key: _driverLinkTokenKey);
      if (token != null && token.isNotEmpty) {
        try {
          await _driverPost('/api/vehicle-tracking/heartbeat', {
            'status': 'stopped',
          }, token: token);
        } catch (_) {}
      }
      await _storage.delete(key: _driverLinkTokenKey);
      await _storage.delete(key: _driverLinkInfoKey);
      if (mounted) {
        setState(() {
          _linked = null;
          _preview = null;
          _codeController.clear();
        });
      }
      await _refreshStatus();
    } catch (e) {
      if (mounted) setState(() => _error = _message(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  List<Widget> _linkedView(BuildContext context, Map<String, dynamic> info) {
    final stopped = _status == 'stopped' || _status == 'not_enabled';
    return [
      Text(
        (info['vehicleName'] ?? 'مركبة').toString(),
        style: Theme.of(context).textTheme.titleLarge,
      ),
      Text((info['organizationName'] ?? '').toString()),
      const SizedBox(height: 8),
      Text(_driverScheduleText(info)),
      const SizedBox(height: 16),
      Text(
        stopped
            ? 'التتبع متوقف. إذا أُلغي الربط أو أعدت تشغيل الجوال، صوّر الباركود من جديد.'
            : trackingStatusLabel(_status),
        style: const TextStyle(fontWeight: FontWeight.bold),
      ),
      const SizedBox(height: 16),
      const Text(
        'يُرسل موقع جوالك للمؤسسة أثناء الدوام فقط، ويتوقف لحاله خارج الدوام. إذا انقطع الإنترنت يُحفظ مسارك خلال الدوام في جوالك ويُرسل عند رجوع الاتصال. تحفظ المؤسسة مسار تحركك خلال الدوام لمدة 30 يومًا. يبقى إشعار التتبع ظاهرًا وتقدر توقفه في أي وقت.',
      ),
      const SizedBox(height: 16),
      OutlinedButton(
        onPressed: _stop,
        child: Text(stopped ? 'ربط من جديد' : 'إيقاف التتبع وفك الربط'),
      ),
    ];
  }

  List<Widget> _previewView(BuildContext context, Map<String, dynamic> info) {
    return [
      Text(
        (info['vehicleName'] ?? 'مركبة').toString(),
        style: Theme.of(context).textTheme.titleLarge,
      ),
      Text((info['organizationName'] ?? '').toString()),
      const SizedBox(height: 8),
      Text(_driverScheduleText(info)),
      if (info['hasDriver'] == true) ...[
        const SizedBox(height: 8),
        const Text(
          'هذه المركبة مرتبطة بجوال سائق آخر، وبموافقتك يصير جوالك هو المرتبط.',
          style: TextStyle(color: Colors.orange),
        ),
      ],
      const SizedBox(height: 16),
      TextField(
        controller: _nameController,
        decoration: const InputDecoration(labelText: 'اسمك'),
      ),
      const SizedBox(height: 12),
      const Text(
        'بعد موافقتك، يُرسل موقع جوالك للمؤسسة أثناء الدوام الذي تحدده المؤسسة فقط، حتى لو كانت الشاشة مقفلة. تحفظ المؤسسة مسار تحركك خلال الدوام وتقدر تراجعه لمدة 30 يومًا. خارج الدوام يتوقف جمع الموقع. إذا انقطع الإنترنت يُحفظ مسارك خلال الدوام في جوالك ويُرسل عند رجوع الاتصال. يبقى إشعار التتبع ظاهرًا وتقدر توقفه في أي وقت.',
      ),
      CheckboxListTile(
        value: _accepted,
        onChanged: (v) => setState(() => _accepted = v ?? false),
        title: const Text(
          'أوافق على تتبع موقع جوالي وحفظ مسار تحركي خلال الدوام',
        ),
      ),
      FilledButton(
        onPressed: _accepted ? _accept : null,
        child: const Text('موافق — ابدأ التتبع'),
      ),
      const SizedBox(height: 10),
      TextButton(
        onPressed: () => setState(() => _preview = null),
        child: const Text('رجوع'),
      ),
    ];
  }

  List<Widget> _scanView(BuildContext context) {
    return [
      const Text(
        'صوّر باركود المركبة الذي يعطيك إياه صاحب المؤسسة، أو اكتب الكود المكتوب تحته.',
      ),
      const SizedBox(height: 16),
      FilledButton.icon(
        onPressed: _scan,
        icon: const Icon(Icons.qr_code_scanner),
        label: const Text('تصوير الباركود'),
      ),
      const SizedBox(height: 20),
      TextField(
        controller: _codeController,
        textCapitalization: TextCapitalization.characters,
        decoration: const InputDecoration(labelText: 'أو اكتب كود المركبة'),
      ),
      const SizedBox(height: 10),
      OutlinedButton(onPressed: _lookup, child: const Text('متابعة')),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final linked = _linked;
    final preview = _preview;
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(title: const Text('أنا سائق')),
        body: _busy
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  if (_error != null) ...[
                    Text(_error!, style: const TextStyle(color: Colors.red)),
                    const SizedBox(height: 12),
                  ],
                  if (linked != null)
                    ..._linkedView(context, linked)
                  else if (preview != null)
                    ..._previewView(context, preview)
                  else
                    ..._scanView(context),
                ],
              ),
      ),
    );
  }
}

class _DriverScanPage extends StatefulWidget {
  const _DriverScanPage();
  @override
  State<_DriverScanPage> createState() => _DriverScanPageState();
}

class _DriverScanPageState extends State<_DriverScanPage> {
  bool _done = false;

  void _onDetect(BarcodeCapture capture) {
    if (_done || capture.barcodes.isEmpty) return;
    final value = capture.barcodes.first.rawValue;
    if (value == null || value.isEmpty) return;
    _done = true;
    Navigator.pop(context, value);
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      appBar: AppBar(title: const Text('صوّر باركود المركبة')),
      body: MobileScanner(onDetect: _onDetect),
    ),
  );
}
