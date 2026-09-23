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
    'مقاولات',
    'زجاج ومرايا',
    'محاماة',
    'تأجير سيارات',
    'مطعم',
    'متجر',
    'صيانة',
    'نشاط آخر',
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
  String organizationName = '';
  List<Map<String, dynamic>> instructions = const [];
  bool loading = true;
  bool saving = false;
  String? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<KhdoomCloudApi> _api() async {
    final prefs = await BranchPreferences.getInstance();
    const storage = FlutterSecureStorage();
    final token = await storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty)
      throw const CloudApiException(401, 'سجل الدخول أولًا');
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  Future<void> _load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final api = await _api();
      try {
        final profile = await api.aiProfile();
        final savedInstructions = await api.aiTrainingInstructions();
        organizationName = profile['organizationName']?.toString().trim() ?? '';
        instructions = savedInstructions
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        activity = profile['activity']?.toString() ?? '';
        customActivityController.text = activities.contains(activity)
            ? ''
            : activity;
        for (final entry in fields.entries) {
          entry.value.text = profile[entry.key]?.toString() ?? '';
        }
      } finally {
        api.close();
      }
    } on CloudApiException catch (e) {
      error = e.message;
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _save() async {
    if (activity.trim().isEmpty) {
      setState(() => error = 'حدد نشاط المؤسسة أولًا');
      return;
    }
    setState(() {
      saving = true;
      error = null;
    });
    try {
      final api = await _api();
      try {
        final payload = <String, dynamic>{'activity': activity.trim()};
        for (final entry in fields.entries) {
          payload[entry.key] = entry.value.text.trim();
        }
        await api.saveAiProfile(payload);
      } finally {
        api.close();
      }
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'تم حفظ ملف موظف ${organizationName.isEmpty ? 'المؤسسة' : organizationName} AI ✓',
            ),
          ),
        );
    } on CloudApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget _field(String key, String label, {String? hint}) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: TextField(
      controller: fields[key],
      maxLines: 4,
      enabled: !saving,
      style: const TextStyle(color: Colors.white),
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        filled: true,
        fillColor: const Color(0xFF172554),
      ),
    ),
  );

  String _statusLabel(String status) => switch (status) {
    'approved' => 'معتمد ويُستخدم الآن',
    'disabled' => 'معطّل',
    _ => 'مسودة — غير مستخدم',
  };

  Color _statusColor(String status) => switch (status) {
    'approved' => const Color(0xFF4ADE80),
    'disabled' => Colors.orangeAccent,
    _ => const Color(0xFF7DD3FC),
  };

  Future<void> _editInstruction([Map<String, dynamic>? existing]) async {
    final content = TextEditingController(
      text: existing?['content']?.toString() ?? '',
    );
    var status = existing?['status']?.toString() ?? 'draft';
    final saved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, setDialogState) => Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            backgroundColor: const Color(0xFF172554),
            title: Text(
              existing == null ? 'تعليم جديد لموظف AI' : 'تعديل التعليم',
              style: const TextStyle(color: Colors.white),
            ),
            content: SizedBox(
              width: 420,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'لن يُستخدم التعليم إلا بعد اختيار «معتمد».',
                    style: TextStyle(color: Colors.white70),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: content,
                    maxLines: 6,
                    autofocus: true,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'اكتب التعليم بلغة طبيعية',
                      filled: true,
                      fillColor: Color(0xFF0B1020),
                    ),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<String>(
                    value: status,
                    dropdownColor: const Color(0xFF172554),
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'حالة التعليم',
                      filled: true,
                      fillColor: Color(0xFF0B1020),
                    ),
                    items: const [
                      DropdownMenuItem(value: 'draft', child: Text('مسودة')),
                      DropdownMenuItem(
                        value: 'approved',
                        child: Text('اعتماد واستخدام'),
                      ),
                      DropdownMenuItem(value: 'disabled', child: Text('تعطيل')),
                    ],
                    onChanged: (value) =>
                        setDialogState(() => status = value ?? 'draft'),
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
                onPressed: () async {
                  if (content.text.trim().isEmpty) return;
                  try {
                    final api = await _api();
                    try {
                      if (existing == null) {
                        await api.createAiTrainingInstruction(
                          'assistant',
                          content.text.trim(),
                          status: status,
                        );
                      } else {
                        await api.updateAiTrainingInstruction(
                          existing['id'] as int,
                          content.text.trim(),
                          status,
                        );
                      }
                    } finally {
                      api.close();
                    }
                    if (dialogContext.mounted)
                      Navigator.pop(dialogContext, true);
                  } on CloudApiException catch (e) {
                    if (dialogContext.mounted)
                      ScaffoldMessenger.of(dialogContext)
                          .showSnackBar(SnackBar(content: Text(e.message)));
                  }
                },
                child: const Text('حفظ'),
              ),
            ],
          ),
        ),
      ),
    );
    content.dispose();
    if (saved == true) await _load();
  }

  Future<void> _deleteInstruction(Map<String, dynamic> item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => Directionality(
        textDirection: TextDirection.rtl,
        child: AlertDialog(
          backgroundColor: const Color(0xFF172554),
          title: const Text(
            'حذف التعليم؟',
            style: TextStyle(color: Colors.white),
          ),
          content: const Text(
            'سيُزال هذا التعليم من ذاكرة الموظف نهائيًا.',
            style: TextStyle(color: Colors.white70),
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
      ),
    );
    if (confirmed != true) return;
    try {
      final api = await _api();
      try {
        await api.deleteAiTrainingInstruction(item['id'] as int);
      } finally {
        api.close();
      }
      await _load();
    } on CloudApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Widget _instructionCard(Map<String, dynamic> item) {
    final status = item['status']?.toString() ?? 'draft';
    return Card(
      color: const Color(0xFF172554),
      child: ListTile(
        title: Text(
          item['content']?.toString() ?? '',
          style: const TextStyle(color: Colors.white, height: 1.4),
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 7),
          child: Text(
            _statusLabel(status),
            style: TextStyle(
              color: _statusColor(status),
              fontWeight: FontWeight.bold,
            ),
          ),
        ),
        trailing: Wrap(
          spacing: 0,
          children: [
            IconButton(
              icon: const Icon(Icons.edit_outlined, color: Color(0xFF7DD3FC)),
              tooltip: 'تعديل',
              onPressed: saving ? null : () => _editInstruction(item),
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
              tooltip: 'حذف',
              onPressed: saving ? null : () => _deleteInstruction(item),
            ),
          ],
        ),
      ),
    );
  }

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
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: Text(
            'تدريب موظف ${organizationName.isEmpty ? 'المؤسسة' : organizationName} AI',
          ),
          actions: [PageRefreshButton(onRefresh: _load)],
        ),
        body: loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    '⚠️ موظف ${organizationName.isEmpty ? 'مؤسستك' : organizationName} AI يتطور حسب تعليمك. كلما علّمته خدماتك وأسلوب التعامل، أصبح أدق في الردود. كل تعليم معزول لمؤسستك.',
                    style: const TextStyle(color: Colors.white70, height: 1.6),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          'التعليمات المحفوظة',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 19,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      FilledButton.icon(
                        onPressed: saving ? null : () => _editInstruction(),
                        icon: const Icon(Icons.add),
                        label: const Text('تعليم جديد'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (instructions.isEmpty)
                    const Text(
                      'لا توجد تعليمات مستقلة بعد. أضف تعليمًا ثم اعتمده ليستخدمه الموظف.',
                      style: TextStyle(color: Colors.white60),
                    )
                  else
                    ...instructions.map(_instructionCard),
                  const Divider(height: 34, color: Color(0xFF38527A)),
                  const Text(
                    'ملف عمل المؤسسة',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 19,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'هذه الإعدادات السابقة تبقى عاملة كما هي، وتكمل التعليمات الجديدة.',
                    style: TextStyle(color: Colors.white60),
                  ),
                  const SizedBox(height: 16),
                  DropdownButtonFormField<String>(
                    initialValue: activities.contains(activity)
                        ? activity
                        : null,
                    dropdownColor: const Color(0xFF172554),
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'نشاط المؤسسة',
                      filled: true,
                      fillColor: Color(0xFF172554),
                    ),
                    items: activities
                        .map((x) => DropdownMenuItem(value: x, child: Text(x)))
                        .toList(),
                    onChanged: saving
                        ? null
                        : (x) {
                            if (x != null) setState(() => activity = x);
                          },
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    enabled: !saving,
                    controller: customActivityController,
                    onChanged: (v) => activity = v,
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'أو اكتب نشاطًا مخصصًا',
                      filled: true,
                      fillColor: Color(0xFF172554),
                    ),
                  ),
                  const SizedBox(height: 16),
                  _field('services', 'الخدمات التي يقدمها النشاط'),
                  _field('service_areas', 'المدن والمناطق التي نخدمها'),
                  _field('working_hours', 'أوقات العمل'),
                  _field('pricing_policy', 'طريقة التسعير'),
                  _field('allowed_prices', 'الأسعار التي يسمح للموظف بعرضها'),
                  _field(
                    'approval_required',
                    'الأسعار أو الخدمات التي تحتاج موافقة بشرية',
                  ),
                  _field(
                    'required_questions',
                    'الأسئلة المهمة التي يجب سؤال العميل عنها',
                  ),
                  _field(
                    'human_handoff',
                    'متى يحوّل الموظف العميل لموظف بشري؟',
                  ),
                  _field('booking_policy', 'سياسة الحجز والمواعيد'),
                  _field('current_offers', 'العروض الحالية'),
                  _field('special_instructions', 'تعليمات خاصة بالمؤسسة'),
                  if (error != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Text(
                        error!,
                        style: const TextStyle(color: Colors.orangeAccent),
                      ),
                    ),
                  FilledButton.icon(
                    onPressed: saving ? null : _save,
                    icon: const Icon(Icons.save_outlined),
                    label: Text(
                      saving ? 'جارٍ الحفظ…' : 'حفظ تدريب موظف خدووم',
                    ),
                  ),
                ],
              ),
      ),
    ),
  );
}
