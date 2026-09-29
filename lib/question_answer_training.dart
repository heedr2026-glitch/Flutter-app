import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'branch_store.dart';
import 'cloud_api.dart';
import 'khdoom_dark_page.dart';
import 'page_refresh_button.dart';

const qaPrefix = 'سؤال وجواب معتمد: ';
Map<String, String>? decodeTrainingQa(String line) {
  if (!line.startsWith(qaPrefix)) return null;
  try {
    final data = jsonDecode(line.substring(qaPrefix.length));
    if (data is Map && data['السؤال'] is String && data['الإجابة'] is String) {
      return {'question': data['السؤال'], 'answer': data['الإجابة']};
    }
  } catch (_) {}
  return null;
}

String mergeTrainingQa(String content, String question, String answer) {
  question = question.trim();
  answer = answer.trim();
  if (question.isEmpty || answer.isEmpty)
    throw const FormatException('اكتب السؤال والإجابة');
  if (question.length > 500 || answer.length > 3000)
    throw const FormatException('السؤال حتى 500 حرف والإجابة حتى 3000 حرف');
  final line = qaPrefix + jsonEncode({'السؤال': question, 'الإجابة': answer});
  final lines = content.split('\n');
  final index = lines.indexWhere(
    (item) => decodeTrainingQa(item)?['question'] == question,
  );
  if (index < 0) {
    lines.add(line);
  } else {
    lines[index] = line;
  }
  final result = lines.join('\n').trim();
  if (result.length > 12000)
    throw const FormatException(
      'مساحة تدريب هذا الموظف ممتلئة. اختصر التدريب قبل إضافة سؤال جديد',
    );
  return result;
}

class QuestionAnswerTrainingPage extends StatefulWidget {
  final String initialEmployee;
  const QuestionAnswerTrainingPage({
    super.key,
    this.initialEmployee = 'reception',
  });
  @override
  State<QuestionAnswerTrainingPage> createState() =>
      _QuestionAnswerTrainingPageState();
}

class _QuestionAnswerTrainingPageState
    extends State<QuestionAnswerTrainingPage> {
  static const types = {
    'reception': 'موظف استقبال العملاء',
    'assistant': 'مساعد المؤسسة',
    'chat': 'تعليم الدردشة السابق (أرشيف)',
    'whatsapp': 'موظف واتساب',
    'calls': 'موظف الاتصالات',
    'commercial_research': 'موظف البحث التجاري',
    'shared': 'جميع الموظفين',
  };
  final _question = TextEditingController();
  final _answer = TextEditingController();
  late String _type;
  final Map<String, String> _content = {};
  bool _loading = true, _saving = false;
  String? _error;
  @override
  void initState() {
    super.initState();
    _type = types.containsKey(widget.initialEmployee)
        ? widget.initialEmployee
        : 'reception';
    _load();
  }

  @override
  void dispose() {
    _question.dispose();
    _answer.dispose();
    super.dispose();
  }

  Future<KhdoomCloudApi> _api() async {
    final prefs = await BranchPreferences.getInstance();
    if (prefs.getString('session_user_type') == 'employee')
      throw const CloudApiException(403, 'حفظ التدريب متاح لمالك المؤسسة فقط');
    final token = await const FlutterSecureStorage().read(
      key: 'cloud_session_token',
    );
    if (token == null || token.isEmpty)
      throw const CloudApiException(401, 'سجل الدخول لحفظ التدريب على الخادم');
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  String _message(Object error) => error is CloudApiException
      ? error.message
      : error is FormatException
      ? error.message
      : 'تعذر الاتصال بالخادم، لم نتأكد من الحفظ. حدّث القائمة قبل إعادة المحاولة';
  Future<void> _load() async {
    if (_saving) return;
    KhdoomCloudApi? api;
    try {
      api = await _api();
      final rows = await api.aiTraining();
      if (!mounted) return;
      setState(() {
        _content.clear();
        for (final row in rows) {
          _content[row['employee_type'].toString()] =
              row['content']?.toString() ?? '';
        }
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = _message(error));
    } finally {
      api?.close();
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _save() async {
    if (_saving) return;
    final question = _question.text.trim(),
        answer = _answer.text.trim(),
        type = _type;
    try {
      mergeTrainingQa('', question, answer);
    } catch (error) {
      setState(() => _error = _message(error));
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    KhdoomCloudApi? api;
    try {
      api = await _api();
      // Read fresh type-specific content; never copy shared training or erase legacy instructions.
      final rows = await api.aiTraining();
      var previous = '';
      for (final row in rows) {
        if (row['employee_type'] == type)
          previous = row['content']?.toString() ?? '';
      }
      final content = mergeTrainingQa(previous, question, answer);
      await api.saveAiTraining(type, content);
      if (!mounted) return;
      setState(() {
        _content[type] = content;
        _question.clear();
        _answer.clear();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'تم حفظ السؤال والإجابة لتدريب ${types[type]} على الخادم',
          ),
        ),
      );
    } catch (error) {
      if (mounted) setState(() => _error = _message(error));
    } finally {
      api?.close();
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => KhdoomDarkPage(
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('التعليم: سؤال وجواب'),
          actions: [PageRefreshButton(onRefresh: _load)],
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text(
                    'اكتب سؤال العميل والإجابة الصحيحة، ثم اضغط حفظ. التدريب السابق يبقى محفوظًا. لا تضع كلمات مرور أو معلومات حساسة.',
                  ),
                  const SizedBox(height: 16),
                  DropdownButtonFormField<String>(
                    initialValue: _type,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'تعليم الموظف',
                    ),
                    items: types.entries
                        .map(
                          (entry) => DropdownMenuItem(
                            value: entry.key,
                            child: Text(entry.value),
                          ),
                        )
                        .toList(),
                    onChanged: _saving
                        ? null
                        : (value) {
                            if (value != null) setState(() => _type = value);
                          },
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _question,
                    enabled: !_saving,
                    maxLength: 500,
                    minLines: 1,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'السؤال',
                      hintText: 'مثال: متى تفتحون؟',
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _answer,
                    enabled: !_saving,
                    maxLength: 3000,
                    minLines: 3,
                    maxLines: 8,
                    decoration: const InputDecoration(
                      labelText: 'الإجابة',
                      hintText: 'اكتب الإجابة التي تريد أن يعتمد عليها الموظف',
                    ),
                  ),
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 8),
                      child: Text(
                        _error!,
                        style: const TextStyle(color: Colors.orangeAccent),
                      ),
                    ),
                  FilledButton.icon(
                    onPressed: _saving ? null : _save,
                    icon: const Icon(Icons.save_outlined),
                    label: Text(
                      _saving ? 'جارٍ الحفظ…' : 'حفظ السؤال والإجابة',
                    ),
                  ),
                  const SizedBox(height: 20),
                  Text(
                    'أسئلة ${types[_type]} المحفوظة',
                    style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 18,
                    ),
                  ),
                  const Text(
                    'اختيار سؤال محفوظ يتيح تحديث إجابته. حفظ السؤال نفسه يستبدل إجابته فقط.',
                  ),
                  for (final line in (_content[_type] ?? '').split('\n'))
                    if (decodeTrainingQa(line) case final qa?)
                      Card(
                        child: ListTile(
                          title: Text(qa['question']!),
                          subtitle: Text(qa['answer']!),
                          onTap: _saving
                              ? null
                              : () {
                                  _question.text = qa['question']!;
                                  _answer.text = qa['answer']!;
                                },
                        ),
                      ),
                ],
              ),
      ),
    ),
  );
}
