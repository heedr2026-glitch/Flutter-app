import 'package:flutter/material.dart';

import 'document_viewer.dart';

class OrganizationRecordEditor extends StatefulWidget {
  const OrganizationRecordEditor({super.key, this.initial});
  final Map<String, dynamic>? initial;

  @override
  State<OrganizationRecordEditor> createState() =>
      _OrganizationRecordEditorState();
}

class _OrganizationRecordEditorState extends State<OrganizationRecordEditor> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _title;
  late final TextEditingController _details;
  late final TextEditingController _website;
  late String _image;
  late String _imageData;
  DateTime? _expiry;

  @override
  void initState() {
    super.initState();
    _title = TextEditingController(
      text: widget.initial?['title']?.toString() ?? '',
    );
    _details = TextEditingController(
      text: widget.initial?['details']?.toString() ?? '',
    );
    _website = TextEditingController(
      text: widget.initial?['website']?.toString() ?? '',
    );
    _image = widget.initial?['imagePath']?.toString() ?? '';
    _imageData = widget.initial?['imageData']?.toString() ?? '';
    _expiry = DateTime.tryParse(widget.initial?['date']?.toString() ?? '');
  }

  @override
  void dispose() {
    _title.dispose();
    _details.dispose();
    _website.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    backgroundColor: const Color(0xFF172554),
    title: Text(
      widget.initial == null ? 'إضافة تنبيه أو مستند' : 'تعديل التنبيه',
      style: const TextStyle(color: Colors.white),
    ),
    content: SizedBox(
      width: 420,
      child: Form(
        key: _form,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: _title,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(
                  labelText: 'اسم الجهة أو المستند',
                ),
                validator: (value) => value == null || value.trim().isEmpty
                    ? 'اكتب اسم المستند'
                    : null,
              ),
              TextFormField(
                controller: _details,
                maxLines: 3,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(
                  labelText: 'المعلومات والملاحظات',
                ),
              ),
              TextFormField(
                controller: _website,
                keyboardType: TextInputType.url,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(
                  labelText: 'رابط الموقع الرسمي',
                ),
                validator: (value) =>
                    value != null &&
                        value.trim().isNotEmpty &&
                        documentWebsite(value) == null
                    ? 'اكتب رابط موقع صحيحًا'
                    : null,
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text(
                  'تاريخ الانتهاء',
                  style: TextStyle(color: Colors.white),
                ),
                subtitle: Text(
                  _expiry == null
                      ? 'غير محدد'
                      : '${_expiry!.day}/${_expiry!.month}/${_expiry!.year}',
                  style: const TextStyle(color: Colors.white70),
                ),
                trailing: const Icon(Icons.event, color: Color(0xFF38BDF8)),
                onTap: () async {
                  final initial = _expiry ?? DateTime.now();
                  final picked = await showDatePicker(
                    context: context,
                    initialDate: initial,
                    firstDate: DateTime(
                      initial.year < 1900 ? initial.year : 1900,
                    ),
                    lastDate: DateTime(
                      initial.year > 2200 ? initial.year : 2200,
                      12,
                      31,
                    ),
                  );
                  if (mounted && picked != null)
                    setState(() => _expiry = picked);
                },
              ),
              DocumentImagePicker(
                path: _image,
                data: _imageData,
                onChanged: (value) => setState(() {
                  _image = value;
                  _imageData = '';
                }),
                onClear: () => setState(() {
                  _image = '';
                  _imageData = '';
                }),
              ),
            ],
          ),
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('إلغاء'),
      ),
      FilledButton(
        onPressed: () {
          if (!_form.currentState!.validate()) return;
          Navigator.pop(context, <String, dynamic>{
            ...?widget.initial,
            'id':
                widget.initial?['id'] ??
                DateTime.now().microsecondsSinceEpoch.toString(),
            'title': _title.text.trim(),
            'details': _details.text.trim(),
            'website': documentWebsite(_website.text)?.toString() ?? '',
            'date': _expiry?.toIso8601String() ?? '',
            'imagePath': _image,
            'imageData': _imageData,
            'icon': widget.initial?['icon'] ?? Icons.folder_copy_outlined,
          });
        },
        child: const Text('حفظ'),
      ),
    ],
  );
}
