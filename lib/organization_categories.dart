import 'package:flutter/material.dart';

const organizationCategoryDefaults = <Map<String, String>>[
  {'id':'balady','title':'الرخصة البلدية'},
  {'id':'cr','title':'السجل التجاري'},
  {'id':'qiwa','title':'اشتراك قوى'},
  {'id':'mudad','title':'حماية الأجور – مدد'},
  {'id':'civil_defense','title':'شهادة الدفاع المدني'},
  {'id':'spl','title':'سبل'},
];

String organizationCategoryOf(Map<String, dynamic> record) {
  final explicit = record['categoryId']?.toString();
  if (explicit != null && explicit.isNotEmpty) return explicit;
  final id = record['id']?.toString();
  for (final category in organizationCategoryDefaults) {
    if (category['id'] == id || category['title'] == record['title']) return category['id']!;
  }
  return 'legacy_${id ?? record['title']}';
}

List<Map<String, String>> organizationCategories(
  Iterable<Map<String, dynamic>> records, List<Map<String, String>>? saved,
) {
  final categories = <String, Map<String, String>>{
    for (final c in saved ?? organizationCategoryDefaults) c['id']!: Map.of(c),
  };
  for (final record in records) {
    final id = organizationCategoryOf(record);
    categories.putIfAbsent(id, () => {'id':id,'title':record['categoryTitle']?.toString() ?? record['title']?.toString() ?? 'مستندات المؤسسة'});
  }
  return categories.values.toList();
}

class OrganizationCategoryNameDialog extends StatefulWidget {
  final String? initial;
  const OrganizationCategoryNameDialog({super.key, this.initial});
  @override
  State<OrganizationCategoryNameDialog> createState() => _OrganizationCategoryNameDialogState();
}

class _OrganizationCategoryNameDialogState extends State<OrganizationCategoryNameDialog> {
  late final TextEditingController controller = TextEditingController(text: widget.initial ?? '');
  @override
  void dispose() { controller.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: Text(widget.initial == null ? 'إضافة تصنيف' : 'تعديل التصنيف', style: const TextStyle(color: Colors.white)),
    content: TextField(controller: controller, autofocus: true, maxLength: 80, style: const TextStyle(color: Colors.white),
      decoration: const InputDecoration(labelText: 'اسم التصنيف', hintText: 'مثال: شهادات الأمن والسلامة', labelStyle: TextStyle(color: Colors.white70), hintStyle: TextStyle(color: Colors.white54)),
      onChanged: (_) => setState(() {})),
    actions: [
      TextButton(onPressed: () => Navigator.pop(context), child: const Text('إلغاء')),
      FilledButton(onPressed: controller.text.trim().isEmpty ? null : () => Navigator.pop(context, controller.text.trim()), child: const Text('حفظ')),
    ],
  );
}
