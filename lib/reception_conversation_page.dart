import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

class ReceptionConversationPage extends StatefulWidget {
  const ReceptionConversationPage({
    super.key,
    this.loadConversation,
    this.sendMessage,
  });
  final Future<Map<String, dynamic>> Function()? loadConversation;
  final Future<Map<String, dynamic>> Function(String, String)? sendMessage;
  @override
  State<ReceptionConversationPage> createState() =>
      _ReceptionConversationPageState();
}

class _ReceptionConversationPageState extends State<ReceptionConversationPage> {
  final _scope = BranchPreferences.getInstance();
  final _input = TextEditingController();
  List<Map<String, dynamic>> _messages = [];
  Map<String, dynamic> _request = {};
  ({String id, String text})? _pending;
  bool _loading = true;
  bool _sending = false;
  bool _ready = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<Map<String, dynamic>> _call({String? message, String? id}) async {
    if (message == null && widget.loadConversation != null)
      return widget.loadConversation!();
    if (message != null && widget.sendMessage != null)
      return widget.sendMessage!(message, id!);
    final prefs = await _scope;
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty)
      throw const CloudApiException(
        401,
        'سجّل الدخول لاستعادة المحادثة وحفظ طلبك',
      );
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      return message == null
          ? await api.receptionConversation()
          : await api.sendReceptionMessage(
              message,
              id!,
              settings: {
                'businessName':
                    prefs.getString('reception_business_name') ?? '',
                'businessInfo':
                    prefs.getString('reception_business_info') ?? '',
                'workingHours':
                    prefs.getString('reception_working_hours') ?? '',
                'replyStyle':
                    prefs.getString('reception_reply_style') ?? 'ودود ومختصر',
              },
            );
    } finally {
      api.close();
    }
  }

  void _apply(Map<String, dynamic> result) {
    _messages = (result['messages'] as List)
        .map((item) => Map<String, dynamic>.from(item as Map))
        .toList();
    _request = Map<String, dynamic>.from(result['request'] as Map? ?? {});
  }

  String _errorText(Object error) => error is CloudApiException
      ? (error.statusCode == 404
            ? 'تحديث المحادثة لم يُفعّل على الخادم بعد.'
            : error.message)
      : 'تعذر الاتصال. لم نتأكد من الحفظ؛ أعد المحاولة.';

  Future<void> _load() async {
    if (_sending) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await _call();
      if (!mounted) return;
      setState(() {
        _apply(result);
        _ready = true;
      });
    } catch (error) {
      if (mounted) setState(() => _error = _errorText(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _send() async {
    if (!_ready || _sending || _loading) return;
    if (_pending == null) {
      final text = _input.text.trim();
      if (text.isEmpty) return;
      final random = Random.secure();
      final id = base64Url.encode(
        List.generate(18, (_) => random.nextInt(256)),
      );
      _pending = (id: id, text: text);
      _input.clear();
    }
    setState(() {
      _sending = true;
      _error = null;
    });
    final pending = _pending!;
    try {
      final result = await _call(message: pending.text, id: pending.id);
      if (!mounted) return;
      setState(() {
        _apply(result);
        _pending = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = _errorText(error));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final visible = [
      ..._messages,
      if (_pending != null) {'sender': 'customer', 'message': _pending!.text},
    ];
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: const Text('محادثة موظفة الاستقبال'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          actions: [
            IconButton(
              tooltip: 'تحديث المحادثة',
              onPressed: _loading || _sending ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: Column(
          children: [
            const Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'محادثة محفوظة لحسابك في هذا الفرع. طلبات التواصل تصل إلى قائمة طلبات المسؤول.',
                style: TextStyle(color: Colors.white70),
              ),
            ),
            if (_request['request_id'] != null)
              Padding(
                padding: const EdgeInsets.all(8),
                child: Text(
                  'طلب #${_request['request_id']} — ${switch (_request['status']) {
                    'waiting_for_manager' => 'بانتظار المسؤول',
                    'completed' => 'مكتمل',
                    'rejected' => 'مرفوض',
                    _ => 'راجع حالة الطلب',
                  }}',
                  style: const TextStyle(color: Color(0xFF7DD3FC)),
                ),
              ),
            if (_loading) const LinearProgressIndicator(),
            Expanded(
              child: visible.isEmpty
                  ? Center(
                      child: Text(
                        _loading
                            ? 'جارٍ استعادة المحادثة...'
                            : 'ابدأ المحادثة برسالة',
                        style: const TextStyle(color: Colors.white54),
                      ),
                    )
                  : ListView.builder(
                      reverse: true,
                      padding: const EdgeInsets.all(12),
                      itemCount: visible.length,
                      itemBuilder: (context, index) {
                        final item = visible[visible.length - 1 - index];
                        final fromUser = item['sender'] == 'customer';
                        return Align(
                          alignment: fromUser
                              ? Alignment.centerRight
                              : Alignment.centerLeft,
                          child: Container(
                            constraints: BoxConstraints(
                              maxWidth: MediaQuery.sizeOf(context).width * .8,
                            ),
                            margin: const EdgeInsets.only(bottom: 10),
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: fromUser
                                  ? const Color(0xFF075E54)
                                  : const Color(0xFF172554),
                              borderRadius: BorderRadius.circular(16),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  fromUser
                                      ? 'أنت'
                                      : item['sender'] == 'human'
                                      ? 'المسؤول'
                                      : 'موظفة الاستقبال',
                                  style: const TextStyle(
                                    color: Colors.white60,
                                    fontSize: 11,
                                  ),
                                ),
                                SelectableText(
                                  item['message'].toString(),
                                  style: const TextStyle(
                                    color: Colors.white,
                                    height: 1.5,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
            ),
            if (_sending)
              const Padding(
                padding: EdgeInsets.all(8),
                child: Text(
                  'جارٍ حفظ الرسالة وتجهيز الرد...',
                  style: TextStyle(color: Colors.white70),
                ),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Text(
                  _error!,
                  style: const TextStyle(color: Colors.amber),
                ),
              ),
            if (_error != null)
              TextButton(
                onPressed: _sending || _loading
                    ? null
                    : _pending == null
                    ? _load
                    : _send,
                child: const Text('إعادة المحاولة'),
              ),
            SafeArea(
              top: false,
              child: Padding(
                padding: const EdgeInsets.all(10),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _input,
                        minLines: 1,
                        maxLines: 4,
                        maxLength: 3000,
                        keyboardType: TextInputType.multiline,
                        style: const TextStyle(color: Colors.white),
                        decoration: InputDecoration(
                          hintText: 'اكتب رسالة',
                          counterText: '',
                          hintStyle: const TextStyle(color: Colors.white54),
                          filled: true,
                          fillColor: const Color(0xFF172554),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(24),
                            borderSide: BorderSide.none,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filled(
                      tooltip: 'إرسال الرسالة',
                      onPressed:
                          !_ready || _sending || _loading || _pending != null
                          ? null
                          : _send,
                      icon: const Icon(Icons.send),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
