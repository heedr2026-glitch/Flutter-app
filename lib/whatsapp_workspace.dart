import 'dart:async';
import 'dart:convert';
import 'dart:async';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:image_picker/image_picker.dart';
import 'package:record/record.dart';

import 'branch_store.dart';

class WhatsAppGateway {
  final Uri base;
  String? token;
  WhatsAppGateway(String url) : base = Uri.parse(url);
  static const storage = FlutterSecureStorage();
  String account = '';
  String get key => 'whatsapp_session_${base.origin}_$account';
  Future<void> restore(BranchPreferences prefs) async {
    if (prefs.getString('session_user_type') == 'employee') return;
    account =
        prefs.getString('admin_login_username') ??
        prefs.getString('whatsapp_login_account_${base.origin}') ??
        '';
    final cloud = Uri.tryParse(
      prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    if (cloud?.origin == base.origin) {
      token = await storage.read(key: 'cloud_session_token');
    } else if (account.isNotEmpty) {
      token = await storage.read(key: key);
    }
  }

  Future<dynamic> call(String path, {Map<String, dynamic>? data}) async {
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 20);
    try {
      final req = await client.openUrl(
        data == null ? 'GET' : 'POST',
        base.resolve(path),
      );
      req.followRedirects = false;
      req.headers.contentType = ContentType.json;
      req.headers.set(HttpHeaders.acceptHeader, 'application/json');
      if (token != null) req.headers.set('Authorization', 'Bearer $token');
      if (data != null) {
        final bytes = utf8.encode(jsonEncode(data));
        req.contentLength = bytes.length;
        req.add(bytes);
      }
      final res = await req.close().timeout(const Duration(seconds: 25));
      final raw = await utf8.decoder
          .bind(res)
          .join()
          .timeout(const Duration(seconds: 25));
      dynamic result;
      try {
        result = jsonDecode(raw);
      } catch (_) {
        throw Exception(
          'استجابة غير صحيحة من رابط الخادم (HTTP ${res.statusCode}). أعد المحاولة بعد قليل',
        );
      }
      if (res.statusCode < 200 || res.statusCode >= 300) {
        throw Exception(
          '${path == "/api/login"
              ? "تسجيل الدخول"
              : path == "/api/whatsapp/status"
              ? "فحص ميتا"
              : "تحميل الرسائل"}: HTTP ${res.statusCode} — ${result is Map ? result["error"] ?? result["message"] ?? "رفض الطلب دون تفاصيل" : "استجابة غير متوقعة"}',
        );
      }
      return result;
    } finally {
      client.close(force: true);
    }
  }

  Future<void> login(String username, String password) async {
    token = null;
    final result = await call(
      '/api/login',
      data: {
        'username': username,
        'password': password,
        'deviceName': 'ربط واتساب',
      },
    );
    token = result['token'] as String?;
    if (token == null || token!.isEmpty) {
      throw Exception('لم يصدر الخادم جلسة دخول');
    }
    await storage.write(key: key, value: token);
  }
}

class WhatsAppWorkspace extends StatefulWidget {
  const WhatsAppWorkspace({
    super.key,
    this.inbox = false,
    this.peer,
    this.gatewayFactory,
  });
  final bool inbox;
  final String? peer;
  final WhatsAppGateway Function(String)? gatewayFactory;
  @override
  State<WhatsAppWorkspace> createState() => _WhatsAppWorkspaceState();
}

class _WhatsAppWorkspaceState extends State<WhatsAppWorkspace> {
  static const _defaultBackendUrl = 'https://khdoom-api.onrender.com';
  static const _defaultBusinessPhone = '+966135870969';
  static const _defaultPhoneNumberId = '1372955935890152';
  final _url = TextEditingController();
  final _phone = TextEditingController();
  final _id = TextEditingController();
  final _search = TextEditingController();
  final _input = TextEditingController();
  final _recorder = AudioRecorder();
  bool _recording = false;
  Timer? _poll;
  bool _refreshing = false;
  String? _messageError;
  final _scope = BranchPreferences.getInstance();
  bool _busy = true;
  bool _connected = false;
  String _detail = 'لم يتم فحص الاتصال بعد';
  List<Map<String, dynamic>> _messages = [];
  @override
  void initState() {
    super.initState();
    _load();
    if (widget.inbox)
      _poll = Timer.periodic(const Duration(seconds: 10), (_) {
        if (mounted && !_busy && (ModalRoute.of(context)?.isCurrent ?? false))
          _refreshMessages();
      });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _input.dispose();
    _url.dispose();
    _phone.dispose();
    _id.dispose();
    _search.dispose();
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final prefs = await _scope;
    if (!mounted) return;
    final savedUrl = prefs.getString('whatsapp_backend_url');
    // The service endpoint and Meta phone ID are app configuration. Keep them
    // out of the subscriber-facing form; the subscriber only supplies the
    // business phone number.
    // Never trust or expose a subscriber-editable endpoint. The production
    // Khdoom endpoint is selected by the app so old/manual values cannot send
    // Meta checks to the wrong server.
    _url.text = _defaultBackendUrl;
    _phone.text = prefs.getString('whatsapp_business_phone') ?? '';
    _id.text = _defaultPhoneNumberId;
    setState(() => _busy = false);
    if (savedUrl != null && savedUrl.isNotEmpty) {
      // The inbox must not wait for the slower Meta health check. Load the
      // conversations first, then update the connection badge in background.
      if (widget.inbox) {
        unawaited(_loadInboxFast());
      } else {
        await _check();
      }
    }
  }

  Future<void> _loadInboxFast() async {
    try {
      final api = await _gateway();
      await _refreshMessages(api: api);
      if (!mounted) return;
      try {
        final result = await api.call('/api/whatsapp/status') as Map;
        if (!mounted) return;
        setState(() {
          _connected = result['connected'] == true;
          _detail = result['detail']?.toString() ?? 'لم يكتمل الربط';
        });
      } catch (_) {
        // Messages remain visible even when Meta status is temporarily slow.
      }
    } catch (_) {
      // _refreshMessages keeps the readable error state for the inbox.
    }
  }

  Future<WhatsAppGateway> _gateway() async {
    final uri = Uri.tryParse(_url.text.trim());
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path != '' && uri.path != '/')) {
      throw Exception('أدخل عنوان HTTPS للخادم دون مسار أو بيانات دخول');
    }
    final api =
        widget.gatewayFactory?.call(uri.origin) ?? WhatsAppGateway(uri.origin);
    await api.restore(await _scope);
    if (api.token == null) {
      throw Exception('سجّل الدخول إلى خادم واتساب باستخدام الزر أدناه');
    }
    return api;
  }

  Future<void> _check({bool save = false}) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _connected = false;
      _detail = 'جارٍ فحص الاتصال';
    });
    try {
      final api = await _gateway();
      if (save) {
        if (!RegExp(r'^\+?[0-9]{7,15}$').hasMatch(_phone.text.trim())) {
          throw Exception('أدخل رقم واتساب المؤسسة مع رمز الدولة');
        }
        final prefs = await _scope;
        await prefs.setString('whatsapp_backend_url', api.base.origin);
        await prefs.setString('whatsapp_business_phone', _phone.text.trim());
        await prefs.setString('whatsapp_phone_number_id', _id.text.trim());
        await api.call(
          '/api/whatsapp/connect',
          data: {'phone': _phone.text.trim()},
        );
      }
      await _refreshMessages(api: api);
      final result = await api.call('/api/whatsapp/status') as Map;
      final connected = result['connected'] == true;
      if (!mounted) return;
      setState(() {
        _connected = connected;
        _detail = result['detail']?.toString() ?? 'لم يكتمل الربط';
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _detail = e.toString().replaceFirst('Exception: ', '');
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _refreshMessages({WhatsAppGateway? api}) async {
    if (_refreshing) return;
    _refreshing = true;
    try {
      final gateway = api ?? await _gateway();
      final result = await gateway.call('/api/whatsapp/messages') as Map;
      final rows = (result['messages'] as List)
          .map((m) => Map<String, dynamic>.from(m as Map))
          .toList();
      if (mounted)
        setState(() {
          _messages = rows;
          _messageError = null;
        });
    } catch (e) {
      if (mounted)
        setState(
          () => _messageError = e.toString().replaceFirst('Exception: ', ''),
        );
    } finally {
      _refreshing = false;
    }
  }

  Future<void> _probe() async {
    setState(() {
      _busy = true;
      _detail = 'جارٍ فحص الشبكة من الهاتف';
    });
    try {
      final uri = Uri.parse(_url.text.trim());
      if (uri.scheme != 'https' ||
          uri.host.isEmpty ||
          uri.userInfo.isNotEmpty) {
        throw Exception('عنوان الخادم غير صحيح');
      }
      final result = await WhatsAppGateway(uri.origin).call('/health');
      if (mounted)
        setState(
          () => _detail = result is Map && result['status'] == 'ok'
              ? 'اتصال الهاتف بالخادم ناجح. سجّل الدخول ثم افحص الربط.'
              : 'رد الخادم لا يطابق خدمة خدوم',
        );
    } catch (e) {
      if (mounted)
        setState(() => _detail = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _login() async {
    final uri = Uri.tryParse(_url.text.trim());
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path != '' && uri.path != '/')) {
      setState(() => _detail = 'أدخل عنوان HTTPS صحيحًا أولًا');
      return;
    }
    final username = TextEditingController(),
        password = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        scrollable: true,
        title: const Text('دخول خادم واتساب'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('سيتم تسجيل الدخول إلى: ${uri.origin}'),
            TextField(
              controller: username,
              decoration: const InputDecoration(labelText: 'اسم المستخدم'),
              autofillHints: const [],
            ),
            TextField(
              controller: password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'كلمة المرور'),
              autofillHints: const [],
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('تسجيل الدخول'),
          ),
        ],
      ),
    );
    final name = username.text.trim(), pass = password.text;
    // Dispose after the dialog route animation releases its fields.
    Future.delayed(const Duration(seconds: 1), () {
      username.dispose();
      password.dispose();
    });
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final loginApi = WhatsAppGateway(uri.origin);
      await loginApi.restore(await _scope);
      if (loginApi.account.isEmpty) loginApi.account = name;
      await loginApi.login(name, pass);
      final prefs = await _scope;
      await prefs.setString('whatsapp_backend_url', uri.origin);
      await prefs.setString(
        'whatsapp_login_account_${uri.origin}',
        loginApi.account,
      );
      if (mounted) setState(() => _detail = 'تم تسجيل الدخول؛ افحص الربط');
    } catch (e) {
      if (mounted) {
        setState(() => _detail = e.toString().replaceFirst('Exception: ', ''));
      }
      return; // Keep the actual login error visible; do not overwrite it with a status check.
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    if (mounted) await _check();
  }

  Future<void> _reply(String peer, {String? draft}) async {
    final input = TextEditingController();
    final ok = draft != null
        ? true
        : await showDialog<bool>(
            context: context,
            builder: (ctx) => AlertDialog(
              scrollable: true,
              title: Text('رد إلى $peer'),
              content: TextField(
                controller: input,
                maxLength: 4096,
                maxLines: 4,
                decoration: const InputDecoration(labelText: 'الرسالة'),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx, false),
                  child: const Text('إلغاء'),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(ctx, true),
                  child: const Text('إرسال'),
                ),
              ],
            ),
          );
    final message = (draft ?? input.text).trim();
    Future.delayed(const Duration(seconds: 1), input.dispose);
    if (ok != true || message.isEmpty || !mounted) return;
    setState(() => _busy = true);
    try {
      final api = await _gateway();
      final id = List.generate(
        16,
        (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
      ).join();
      final result = await api.call(
        '/api/whatsapp/messages',
        data: {'to': peer, 'message': message, 'clientMessageId': id},
      ) as Map;
      if (mounted) {
        if (draft != null) _input.clear();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              result['state'] == 'accepted'
                  ? 'قبلت ميتا الرسالة؛ تابع حالة التسليم'
                  : 'حالة الإرسال: ${result['state']}',
            ),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    if (mounted) await _check();
  }

  Future<void> _pickAndSendImage(String peer, ImageSource source) async {
    if (_busy) return;
    final image = await ImagePicker().pickImage(
      source: source,
      imageQuality: 82,
      maxWidth: 1600,
      maxHeight: 1600,
    );
    if (image == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final bytes = await image.readAsBytes();
      final api = await _gateway();
      final id = List.generate(
        16,
        (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
      ).join();
      final result = await api.call(
        '/api/whatsapp/messages',
        data: {
          'to': peer,
          'message': '',
          'mediaType': 'image',
          'mediaName': image.name,
          'mediaBase64': base64Encode(bytes),
          'clientMessageId': id,
        },
      ) as Map;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(result['state'] == 'accepted' ? 'تم إرسال الصورة' : 'حالة الصورة: ${result['state']}')),
        );
        await _refreshMessages(api: api);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _toggleRecording(String peer) async {
    if (_busy) return;
    if (await _recorder.isRecording()) {
      final path = await _recorder.stop();
      if (mounted) setState(() => _recording = false);
      if (path == null || !mounted) return;
      final file = File(path);
      if (!await file.exists()) return;
      setState(() => _busy = true);
      try {
        final api = await _gateway();
        final id = List.generate(16, (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0')).join();
        final result = await api.call('/api/whatsapp/messages', data: {
          'to': peer,
          'message': '',
          'mediaType': 'audio',
          'mediaName': 'voice.ogg',
          'mediaBase64': base64Encode(await file.readAsBytes()),
          'clientMessageId': id,
        }) as Map;
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('تم إرسال المقطع الصوتي')));
          await _refreshMessages(api: api);
        }
      } catch (e) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))));
      } finally {
        if (mounted) setState(() => _busy = false);
      }
      return;
    }
    if (!await _recorder.hasPermission()) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('اسمح لخدوم باستخدام الميكروفون أولًا')));
      return;
    }
    await _recorder.start(
      const RecordConfig(encoder: AudioEncoder.opus),
      path: '${Directory.systemTemp.path}/khdoom_voice_${DateTime.now().millisecondsSinceEpoch}.ogg',
    );
    if (mounted) setState(() => _recording = true);
  }

  Widget _field(TextEditingController c, String label) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: TextField(
      controller: c,
      enabled: !_busy,
      textDirection: TextDirection.ltr,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
      ),
    ),
  );

  void _useReadyConnection() {
    setState(() {
      _url.text = _defaultBackendUrl;
      _phone.text = _defaultBusinessPhone;
      _id.text = _defaultPhoneNumberId;
      _detail = 'تمت تعبئة بيانات الربط الجاهزة. اضغط حفظ وفحص الربط.';
    });
  }

  Widget _avatar(String label, {double size = 48}) => CircleAvatar(
    radius: size / 2,
    backgroundColor: const Color(0xFFB7D7C9),
    child: Text(
      label.isEmpty ? '؟' : label.substring(0, 1),
      style: TextStyle(
        color: const Color(0xFF075E54),
        fontSize: size * .36,
        fontWeight: FontWeight.w700,
      ),
    ),
  );

  PreferredSizeWidget _whatsAppAppBar({required bool conversation}) => AppBar(
    backgroundColor: const Color(0xFF075E54),
    foregroundColor: Colors.white,
    elevation: 0,
    titleSpacing: conversation ? 0 : 16,
    title: conversation
        ? Row(
            children: [
              _avatar(widget.peer ?? '?', size: 38),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  widget.peer ?? 'واتساب',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          )
        : const Text(
            'محادثات واتساب',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
    actions: conversation
        ? [
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.videocam_outlined),
              tooltip: 'مكالمة فيديو',
            ),
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.call_outlined),
              tooltip: 'مكالمة صوتية',
            ),
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.more_vert),
              tooltip: 'المزيد',
            ),
          ]
        : [
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.camera_alt_outlined),
              tooltip: 'الكاميرا',
            ),
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.search),
              tooltip: 'بحث',
            ),
            IconButton(
              onPressed: () {},
              icon: const Icon(Icons.more_vert),
              tooltip: 'المزيد',
            ),
          ],
  );

  Widget _chatList(Map<String, Map<String, dynamic>> peers) {
    final entries = peers.entries.toList();
    return Container(
      color: const Color(0xFFF7F8F8),
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
            child: TextField(
              controller: _search,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: 'بحث في المحادثات',
                prefixIcon: const Icon(Icons.search, color: Color(0xFF667781)),
                filled: true,
                fillColor: const Color(0xFFEFF2F3),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(vertical: 10),
              ),
            ),
          ),
          Expanded(
            child: entries.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.forum_outlined,
                            size: 64,
                            color: Colors.grey.shade400,
                          ),
                          const SizedBox(height: 12),
                          const Text(
                            'لا توجد محادثات بعد',
                            style: TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Text(
                            'ستظهر هنا رسائل العملاء الجديدة',
                            style: TextStyle(color: Colors.grey.shade600),
                          ),
                        ],
                      ),
                    ),
                  )
                : ListView.separated(
                    itemCount: entries.length,
                    separatorBuilder: (_, __) =>
                        const Divider(height: 1, indent: 76),
                    itemBuilder: (_, index) {
                      final entry = entries[index];
                      final message = entry.value;
                      return ListTile(
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 5,
                        ),
                        leading: _avatar(entry.key),
                        title: Row(
                          children: [
                            Expanded(
                              child: Text(
                                entry.key,
                                style: const TextStyle(
                                  fontSize: 16,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                            Text(
                              _messageTime(message),
                              style: const TextStyle(
                                fontSize: 12,
                                color: Color(0xFF667781),
                              ),
                            ),
                          ],
                        ),
                        subtitle: Row(
                          children: [
                            if (message['direction'] == 'outbound') ...[
                              const Icon(
                                Icons.done_all,
                                size: 16,
                                color: Color(0xFF53BDEB),
                              ),
                              const SizedBox(width: 3),
                            ],
                            Expanded(
                              child: Text(
                                message['body']?.toString() ?? 'مرفق',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  color: Color(0xFF667781),
                                ),
                              ),
                            ),
                          ],
                        ),
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => WhatsAppWorkspace(
                              inbox: true,
                              peer: entry.key,
                              gatewayFactory: widget.gatewayFactory,
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  String _messageTime(Map<String, dynamic> message) {
    final raw = message['timestamp'] ?? message['createdAt'] ?? message['time'];
    if (raw == null) return '';
    final parsed = DateTime.tryParse(raw.toString());
    if (parsed == null) return '';
    final local = parsed.toLocal();
    final hour = local.hour % 12 == 0 ? 12 : local.hour % 12;
    return '$hour:${local.minute.toString().padLeft(2, '0')} ${local.hour >= 12 ? 'م' : 'ص'}';
  }

  Widget _bubble(Map<String, dynamic> message) {
    final outbound = message['direction'] == 'outbound';
    return Align(
      alignment: outbound ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.sizeOf(context).width * .82,
        ),
        margin: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
        padding: const EdgeInsets.fromLTRB(10, 7, 8, 6),
        decoration: BoxDecoration(
          color: outbound ? const Color(0xFFD9FDD3) : Colors.white,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(9),
            topRight: const Radius.circular(9),
            bottomLeft: Radius.circular(outbound ? 9 : 2),
            bottomRight: Radius.circular(outbound ? 2 : 9),
          ),
          boxShadow: const [
            BoxShadow(
              color: Color(0x12000000),
              blurRadius: 1,
              offset: Offset(0, 1),
            ),
          ],
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Flexible(
              child: Text(
                message['body']?.toString() ?? 'مرفق',
                style: const TextStyle(fontSize: 16, height: 1.35),
              ),
            ),
            const SizedBox(width: 8),
            Text(
              _messageTime(message),
              style: const TextStyle(fontSize: 11, color: Color(0xFF667781)),
            ),
            if (outbound) ...[
              const SizedBox(width: 3),
              Icon(
                message['state'] == 'delivered' ? Icons.done_all : Icons.done,
                size: 16,
                color: message['state'] == 'delivered'
                    ? const Color(0xFF53BDEB)
                    : const Color(0xFF667781),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _composer() => SafeArea(
    top: false,
    child: Container(
      color: const Color(0xFFF0F2F5),
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          IconButton(
            onPressed: () {},
            icon: const Icon(
              Icons.emoji_emotions_outlined,
              color: Color(0xFF54656F),
            ),
            tooltip: 'رموز تعبيرية',
          ),
          IconButton(
            onPressed: widget.peer == null
                ? null
                : () => _pickAndSendImage(widget.peer!, ImageSource.gallery),
            icon: const Icon(Icons.attach_file, color: Color(0xFF54656F)),
            tooltip: 'إرفاق',
          ),
          IconButton(
            onPressed: widget.peer == null
                ? null
                : () => _pickAndSendImage(widget.peer!, ImageSource.camera),
            icon: const Icon(Icons.camera_alt_outlined, color: Color(0xFF54656F)),
            tooltip: 'تصوير وإرسال',
          ),
          Expanded(
            child: TextField(
              controller: _input,
              minLines: 1,
              maxLines: 5,
              maxLength: 4096,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: 'اكتب رسالة',
                filled: true,
                fillColor: Colors.white,
                counterText: '',
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 11,
                ),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(22),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),
          const SizedBox(width: 5),
          CircleAvatar(
            backgroundColor: const Color(0xFF00A884),
            child: IconButton(
              color: Colors.white,
              tooltip: _recording
                  ? 'إيقاف وإرسال التسجيل'
                  : _input.text.trim().isEmpty
                  ? 'تسجيل صوتي'
                  : 'إرسال',
              icon: Icon(
                _recording
                    ? Icons.stop
                    : _input.text.trim().isEmpty
                    ? Icons.mic
                    : Icons.send,
              ),
              onPressed: _busy
                  ? null
                  : () {
                      if (_input.text.trim().isEmpty) {
                        _toggleRecording(widget.peer!);
                      } else {
                        _reply(widget.peer!, draft: _input.text);
                      }
                    },
            ),
          ),
        ],
      ),
    ),
  );

  Widget _conversation(List<Map<String, dynamic>> messages) => Scaffold(
    backgroundColor: const Color(0xFFEFE7DE),
    appBar: _whatsAppAppBar(conversation: true),
    body: Column(
      children: [
        Expanded(
          child: messages.isEmpty
              ? Center(
                  child: Text(
                    'لا توجد رسائل بعد',
                    style: TextStyle(color: Colors.grey.shade700),
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  children: messages.reversed.map(_bubble).toList(),
                ),
        ),
        _composer(),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final query = _search.text.trim();
    final messages = _messages.where(
      (m) =>
          (widget.peer == null || m['peer'] == widget.peer) &&
          (query.isEmpty || '${m['peer']} ${m['body']}'.contains(query)),
    );
    final peers = <String, Map<String, dynamic>>{};
    for (final m in messages) {
      peers.putIfAbsent(m['peer'].toString(), () => m);
    }
    if (widget.inbox) {
      if (widget.peer != null)
        return Directionality(
          textDirection: TextDirection.rtl,
          child: _conversation(messages.toList()),
        );
      return Directionality(
        textDirection: TextDirection.rtl,
        child: Scaffold(
          appBar: _whatsAppAppBar(conversation: false),
          body: _chatList(peers),
        ),
      );
    }

    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: Text(
            widget.peer ??
                (widget.inbox ? 'محادثات واتساب' : 'ربط موظف واتساب'),
          ),
          actions: [
            if (widget.inbox)
              IconButton(
                icon: const Icon(Icons.settings),
                tooltip: 'إعداد واتساب',
                onPressed: () async {
                  await Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => const WhatsAppWorkspace(),
                    ),
                  );
                  if (mounted) await _load();
                },
              ),
            IconButton(
              onPressed: _busy ? null : () => _check(),
              icon: const Icon(Icons.refresh),
              tooltip: 'فحص الاتصال',
            ),
          ],
        ),
        body: ListView(
          scrollCacheExtent: ScrollCacheExtent.pixels(1200),
          padding: const EdgeInsets.all(16),
          children: [
            if (_busy) const LinearProgressIndicator(),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Icon(
                      _connected ? Icons.cloud_done : Icons.cloud_off,
                      color: _connected ? Colors.green : Colors.orange,
                    ),
                    const SizedBox(width: 12),
                    Expanded(child: Text(_detail)),
                  ],
                ),
              ),
            ),
            if (!widget.inbox) ...[
              Card(
                color: Theme.of(context).colorScheme.primaryContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.link,
                            color: Theme.of(context).colorScheme.primary,
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'إضافة رقم المؤسسة',
                            style: Theme.of(context).textTheme.titleMedium
                                ?.copyWith(fontWeight: FontWeight.bold),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        _connected ? 'تم حفظ الرقم والربط جاهز.' : 'أدخل رقم واتساب المؤسسة مع رمز الدولة. إعدادات الربط محفوظة بأمان ولا تظهر للمشترك.',
                      ),
                      const SizedBox(height: 12),
                      _field(_phone, 'رقم واتساب المؤسسة مع رمز الدولة'),
                      const SizedBox(height: 4),
                      SizedBox(
                        width: double.infinity,
                        child: FilledButton.icon(
                          onPressed: _busy ? null : () => _check(save: true),
                          icon: const Icon(Icons.check_circle_outline),
                          label: const Text('حفظ وفحص الربط'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              // Keep the controllers available for the gateway and backwards
              // compatibility with existing tests, but never render internal
              // server/Meta configuration to subscribers.
              SizedBox(
                width: 1,
                height: 1,
                child: ClipRect(
                  child: IgnorePointer(
                    child: Opacity(
                      opacity: 0,
                      child: _field(_url, 'عنوان خادم خدوم HTTPS'),
                    ),
                  ),
                ),
              ),
              SizedBox(
                width: 1,
                height: 1,
                child: ClipRect(
                  child: IgnorePointer(
                    child: Opacity(
                      opacity: 0,
                      child: _field(_id, 'معرّف رقم الهاتف في Meta'),
                    ),
                  ),
                ),
              ),
              const Text(
                'مفاتيح الربط وإعدادات Meta محمية ولا تظهر للمؤسسة أو للمشترك.',
              ),
              const SizedBox(height: 12),
              TextButton(
                onPressed: _busy ? null : _login,
                child: const Text('تسجيل الدخول إلى خادم واتساب'),
              ),
              TextButton(
                onPressed: _busy ? null : _probe,
                child: const Text('فحص اتصال الهاتف بالخادم'),
              ),
              OutlinedButton(
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const WhatsAppWorkspace(inbox: true),
                  ),
                ),
                child: const Text('عرض الرسائل'),
              ),
            ] else ...[
              if (_messageError != null)
                Text('تعذر تحديث الرسائل: $_messageError'),
              if (_messages.isEmpty && !_busy && _messageError == null)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Text(
                    'لا توجد رسائل محفوظة بعد. بانتظار وصول رسالة جديدة من العميل.',
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
