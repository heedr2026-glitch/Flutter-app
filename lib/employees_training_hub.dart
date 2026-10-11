import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'ai_employee_training_page.dart';
import 'app_features.dart';
import 'branch_store.dart';
import 'cloud_api.dart';
import 'main.dart' show AiTrainingPage;
import 'question_answer_training.dart';

/// «تعليم الموظفين»: صفحة وحدة لتعليم كل الموظفين الأذكياء.
/// فوق تختار لمين التعليم (الكل افتراضيًا)، وتحتها أسئلة الموظفين اللي تنتظر جوابك.
class EmployeesTrainingHubPage extends StatefulWidget {
  const EmployeesTrainingHubPage({super.key});

  @override
  State<EmployeesTrainingHubPage> createState() =>
      _EmployeesTrainingHubPageState();
}

class _EmployeesTrainingHubPageState extends State<EmployeesTrainingHubPage> {
  static const _storage = FlutterSecureStorage();
  String _target = 'shared';
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _questions = [];
  final Map<String, TextEditingController> _answers = {};
  final Map<String, bool> _saveForAll = {};
  final Set<String> _sending = {};

  Map<String, String> get _targets => {
    'shared': 'الكل',
    'assistant': 'اسألني',
    'reception': 'المحادثة الخارجية',
    if (KhdoomAppFeatures.whatsapp) 'whatsapp': 'الواتساب',
    if (KhdoomAppFeatures.calls) 'calls': 'المكالمات',
    'commercial_research': 'البحث التجاري',
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final controller in _answers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<KhdoomCloudApi?> _api() async {
    final token = await _storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return null;
    final prefs = await BranchPreferences.getInstance();
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final api = await _api();
    if (api == null) {
      setState(() {
        _loading = false;
        _error = 'سجّل دخولك بحساب المدير عشان تشوف أسئلة الموظفين.';
      });
      return;
    }
    try {
      final items = await api.aiQuestions();
      if (!mounted) return;
      setState(() {
        _questions = items
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    } finally {
      api.close();
    }
  }

  Future<void> _answer(Map<String, dynamic> item) async {
    final id = item['id'].toString();
    final controller = _answers[id];
    final text = controller?.text.trim() ?? '';
    if (text.length < 2) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('اكتب الجواب أول')));
      return;
    }
    final saveFor = (_saveForAll[id] ?? true)
        ? 'shared'
        : (item['employee_type']?.toString() == 'chat'
              ? 'reception'
              : item['employee_type']?.toString() ?? 'shared');
    setState(() => _sending.add(id));
    final api = await _api();
    try {
      if (api == null) throw StateError('سجّل دخولك من جديد');
      final result = await api.answerAiQuestion(item['id'], text, saveFor);
      if (!mounted) return;
      setState(() {
        _questions.removeWhere((q) => q['id'].toString() == id);
        _answers.remove(id)?.dispose();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            result['deliveredToCustomer'] == true
                ? 'وصل جوابك للعميل وانحفظ في التعليم ✓'
                : 'انحفظ جوابك في التعليم ✓',
          ),
        ),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    } finally {
      api?.close();
      if (mounted) setState(() => _sending.remove(id));
    }
  }

  Widget _methodCard({
    required IconData icon,
    required String title,
    required String subtitle,
    required VoidCallback onTap,
  }) {
    return Card(
      color: const Color(0xFF172554),
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        leading: Icon(icon, color: const Color(0xFF38BDF8)),
        title: Text(
          title,
          style: const TextStyle(
            color: Colors.white,
            fontWeight: FontWeight.bold,
          ),
        ),
        subtitle: Text(
          subtitle,
          style: const TextStyle(color: Colors.white70),
        ),
        trailing: const Icon(Icons.chevron_left, color: Colors.white54),
        onTap: onTap,
      ),
    );
  }

  Widget _questionCard(Map<String, dynamic> item) {
    final id = item['id'].toString();
    final controller = _answers.putIfAbsent(id, TextEditingController.new);
    final forAll = _saveForAll[id] ?? true;
    final customer = (item['customer_question'] ?? '').toString();
    return Card(
      color: const Color(0xFF1E293B),
      margin: const EdgeInsets.only(bottom: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: const BorderSide(color: Color(0xFFFBBF24)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '❓ من ${item['employee_name'] ?? 'الموظف'}',
              style: const TextStyle(
                color: Color(0xFFFBBF24),
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              (item['question'] ?? '').toString(),
              style: const TextStyle(color: Colors.white, fontSize: 16),
            ),
            if (customer.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'العميل قال: «$customer»',
                  style: const TextStyle(color: Colors.white60),
                ),
              ),
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              maxLines: 3,
              minLines: 1,
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(
                hintText: 'اكتب الجواب',
                hintStyle: TextStyle(color: Colors.white38),
                filled: true,
                fillColor: Color(0xFF0B1020),
              ),
            ),
            Row(
              children: [
                Checkbox(
                  value: forAll,
                  onChanged: (value) =>
                      setState(() => _saveForAll[id] = value ?? true),
                ),
                const Expanded(
                  child: Text(
                    'احفظ الجواب لكل الموظفين',
                    style: TextStyle(color: Colors.white70),
                  ),
                ),
                FilledButton(
                  onPressed: _sending.contains(id) ? null : () => _answer(item),
                  child: Text(_sending.contains(id) ? 'جاري…' : 'أرسل الجواب'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final targets = _targets;
    if (!targets.containsKey(_target)) _target = 'shared';
    final targetName = targets[_target] ?? 'الكل';
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('تعليم الموظفين'),
          actions: [
            IconButton(
              tooltip: 'تحديث',
              onPressed: _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: _load,
          child: ListView(
            padding: const EdgeInsets.all(16),
            children: [
              const Text(
                'لمين التعليم؟',
                style: TextStyle(color: Colors.white70),
              ),
              const SizedBox(height: 6),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final entry in targets.entries)
                    ChoiceChip(
                      label: Text(entry.value),
                      selected: _target == entry.key,
                      onSelected: (_) => setState(() => _target = entry.key),
                    ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                _target == 'shared'
                    ? 'اللي تعلّمه هنا يعرفه كل الموظفين.'
                    : 'اللي تعلّمه هنا يعرفه «$targetName» بس.',
                style: const TextStyle(color: Colors.white54),
              ),
              const SizedBox(height: 16),
              Text(
                _questions.isEmpty
                    ? 'أسئلة تنتظر جوابك'
                    : 'أسئلة تنتظر جوابك (${_questions.length})',
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 8),
              if (_loading)
                const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: CircularProgressIndicator()),
                )
              else if (_error != null)
                Text(_error!, style: const TextStyle(color: Colors.redAccent))
              else if (_questions.isEmpty)
                const Text(
                  'ما فيه أسئلة الحين 👍 إذا موظف ما عرف يجاوب عميل، يطلع سؤاله هنا ويوصلك إشعار.',
                  style: TextStyle(color: Colors.white60),
                )
              else
                ..._questions.map(_questionCard),
              const SizedBox(height: 16),
              const Text(
                'طرق التعليم',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(height: 8),
              _methodCard(
                icon: Icons.edit_note,
                title: 'اكتب معلومة',
                subtitle: 'علّم «$targetName» معلومة عن شغلك بكلامك',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => AiTrainingPage(initialType: _target),
                  ),
                ),
              ),
              _methodCard(
                icon: Icons.quiz_outlined,
                title: 'سؤال وجواب',
                subtitle: 'اكتب سؤال العميل وجوابه الصحيح',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) =>
                        QuestionAnswerTrainingPage(initialEmployee: _target),
                  ),
                ),
              ),
              _methodCard(
                icon: Icons.storefront_outlined,
                title: 'بيانات المؤسسة',
                subtitle: 'الخدمات وأوقات العمل والأسعار والعروض',
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const AiEmployeeTrainingPage(),
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
