import 'package:flutter/material.dart';
import 'branch_store.dart';
import 'cloud_api.dart';
import 'khdoom_dark_page.dart';
import 'page_refresh_button.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class AiEmployeeTrainingPage extends StatefulWidget {
  const AiEmployeeTrainingPage({super.key});

  @override
  State<AiEmployeeTrainingPage> createState() => _AiEmployeeTrainingPageState();
}

class _AiEmployeeTrainingPageState extends State<AiEmployeeTrainingPage> {
  static const activities = <String>[
    'مقاولات', 'زجاج ومرايا', 'محاماة', 'تأجير سيارات', 'مطعم', 'متجر', 'صيانة', 'نشاط آخر',
  ];
  final fields = <String, TextEditingController>{
    'services': TextEditingController(),
    'service_areas': TextEditingController(),
    'working_hours': TextEditingController(),
    'pricing_policy': TextEditingController(),
    'allowed_prices': TextEditingController(),
    'approval_required': TextEditingController(),
    'required_questions': TextEditingController(),
    'human_handoff': TextEditingController(),
    'booking_policy': TextEditingController(),
    'current_offers': TextEditingController(),
    'special_instructions': TextEditingController(),
  };
  final customActivityController = TextEditingController();
  String activity = '';
  bool loading = true;
  bool saving = false;
  String? error;

  @override
  void initState() { super.initState(); _load(); }

  Future<KhdoomCloudApi> _api() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) throw const CloudApiException(401, 'سجل الدخول أولًا');
    return KhdoomCloudApi(scope: prefs, baseUrl: prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com')..token = token;
  }

  Future<void> _load() async {
    setState(() { loading = true; error = null; });
    try {
      final api = await _api();
      try {
        final profile = await api.aiProfile();
        activity = profile['activity']?.toString() ?? '';
        customActivityController.text = activities.contains(activity) ? '' : activity;
        for (final entry in fields.entries) {
          entry.value.text = profile[entry.key]?.toString() ?? '';
        }
      } finally { api.close(); }
    } on CloudApiException catch (e) { error = e.message; }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _save() async {
    if (activity.trim().isEmpty) { setState(() => error = 'حدد نشاط المؤسسة أولًا'); return; }
    setState(() { saving = true; error = null; });
    try {
      final api = await _api();
      try {
        final payload = <String, dynamic>{'activity': activity.trim()};
        for (final entry in fields.entries) {
          payload[entry.key] = entry.value.text.trim();
        }
        await api.saveAiProfile(payload);
      } finally { api.close(); }
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('تم حفظ تدريب موظف خدووم ✓')));
    } on CloudApiException catch (e) { if (mounted) setState(() => error = e.message); }
    finally { if (mounted) setState(() => saving = false); }
  }

  Widget _field(String key, String label, {String? hint}) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: TextField(controller: fields[key], maxLines: 4, enabled: !saving,
      style: const TextStyle(color: Colors.white), decoration: InputDecoration(labelText: label, hintText: hint, filled: true, fillColor: const Color(0xFF172554))),
  );

  @override
  void dispose() {
    for (final c in fields.values) {
      c.dispose();
    }
    customActivityController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => KhdoomDarkPage(
    child: Directionality(textDirection: TextDirection.rtl, child: Scaffold(
      backgroundColor: const Color(0xFF0B1020),
      appBar: AppBar(title: const Text('تدريب موظف خدووم'), actions: [PageRefreshButton(onRefresh: _load)]),
      body: loading ? const Center(child: CircularProgressIndicator()) : ListView(padding: const EdgeInsets.all(16), children: [
        const Text('حدد نشاط المؤسسة واكتب تعليماتها. يستخدم خدووم نفس نموذج AI، لكن يغيّر أسئلته وردوده تلقائيًا حسب هذا الملف، مع عزل بيانات كل مؤسسة.', style: TextStyle(color: Colors.white70, height: 1.6)),
        const SizedBox(height: 16),
        DropdownButtonFormField<String>(initialValue: activities.contains(activity) ? activity : null, dropdownColor: const Color(0xFF172554), style: const TextStyle(color: Colors.white), decoration: const InputDecoration(labelText: 'نشاط المؤسسة', filled: true, fillColor: Color(0xFF172554)), items: activities.map((x) => DropdownMenuItem(value: x, child: Text(x))).toList(), onChanged: saving ? null : (x) { if (x != null) setState(() => activity = x); }),
        const SizedBox(height: 10),
        TextField(enabled: !saving, controller: customActivityController, onChanged: (v) => activity = v, style: const TextStyle(color: Colors.white), decoration: const InputDecoration(labelText: 'أو اكتب نشاطًا مخصصًا', filled: true, fillColor: Color(0xFF172554))),
        const SizedBox(height: 16),
        _field('services', 'الخدمات التي يقدمها النشاط'),
        _field('service_areas', 'المدن والمناطق التي نخدمها'),
        _field('working_hours', 'أوقات العمل'),
        _field('pricing_policy', 'طريقة التسعير'),
        _field('allowed_prices', 'الأسعار التي يسمح للموظف بعرضها'),
        _field('approval_required', 'الأسعار أو الخدمات التي تحتاج موافقة بشرية'),
        _field('required_questions', 'الأسئلة المهمة التي يجب سؤال العميل عنها'),
        _field('human_handoff', 'متى يحوّل الموظف العميل لموظف بشري؟'),
        _field('booking_policy', 'سياسة الحجز والمواعيد'),
        _field('current_offers', 'العروض الحالية'),
        _field('special_instructions', 'تعليمات خاصة بالمؤسسة'),
        if (error != null) Padding(padding: const EdgeInsets.only(bottom: 10), child: Text(error!, style: const TextStyle(color: Colors.orangeAccent))),
        FilledButton.icon(onPressed: saving ? null : _save, icon: const Icon(Icons.save_outlined), label: Text(saving ? 'جارٍ الحفظ…' : 'حفظ تدريب موظف خدووم')),
      ]),
    )),
  );
}
