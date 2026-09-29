import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

String? commercialWhatsAppPhone(String input) {
  var value = input.trim();
  for (var i = 0; i < 10; i++) {
    value = value
        .replaceAll('٠١٢٣٤٥٦٧٨٩'[i], '$i')
        .replaceAll('۰۱۲۳۴۵۶۷۸۹'[i], '$i');
  }
  value = value.replaceAll(RegExp(r'[\s()\-]'), '');
  if (value.startsWith('+')) {
    value = value.substring(1);
  } else if (value.startsWith('00')) {
    value = value.substring(2);
  } else if (RegExp(r'^05[0-9]{8}$').hasMatch(value)) {
    // This commercial research flow targets Saudi Arabia.
    value = '966${value.substring(1)}';
  }
  if (!RegExp(r'^[1-9][0-9]{7,14}$').hasMatch(value)) return null;
  return value;
}

Future<void> showCommercialWhatsApp(
  BuildContext context, {
  required String projectName,
  String phone = '',
  Future<bool> Function(Uri)? openUrl,
}) async {
  final uri = await showDialog<Uri>(
    context: context,
    builder: (_) => _WhatsAppDraft(projectName: projectName, phone: phone),
  );
  if (uri == null || !context.mounted) return;
  try {
    final opened =
        await (openUrl?.call(uri) ??
            launchUrl(uri, mode: LaunchMode.externalApplication));
    if (opened) return;
  } catch (_) {
    // A missing handler and platform errors share the same recoverable UI.
  }
  if (context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('تعذر فتح واتساب على هذا الجهاز')),
    );
  }
}

class _WhatsAppDraft extends StatefulWidget {
  const _WhatsAppDraft({required this.projectName, required this.phone});
  final String projectName;
  final String phone;
  @override
  State<_WhatsAppDraft> createState() => _WhatsAppDraftState();
}

class _WhatsAppDraftState extends State<_WhatsAppDraft> {
  final _form = GlobalKey<FormState>();
  late final _phone = TextEditingController(text: widget.phone);
  late final _message = TextEditingController(
    text:
        'السلام عليكم، معكم فريق متخصص في توريد وتركيب الزجاج والواجهات. '
        'اطلعنا على مشروع ${widget.projectName} ويسعدنا خدمتكم. '
        'نرجو تزويدنا بالمخططات أو المقاسات والتفاصيل لإعداد العرض المناسب.',
  );
  @override
  void dispose() {
    _phone.dispose();
    _message.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: AlertDialog(
      title: const Text('تواصل عبر واتساب'),
      content: SingleChildScrollView(
        child: Form(
          key: _form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: _phone,
                onChanged: (_) => setState(() {}),
                textDirection: TextDirection.ltr,
                keyboardType: TextInputType.phone,
                decoration: const InputDecoration(
                  labelText: 'رقم واتساب مع رمز الدولة',
                  hintText: '+9665XXXXXXXX',
                ),
                validator: (value) =>
                    commercialWhatsAppPhone(value ?? '') == null
                    ? 'أدخل رقمًا صحيحًا مع رمز الدولة'
                    : null,
              ),
              const SizedBox(height: 8),
              const Text(
                'الأرقام المحلية السعودية التي تبدأ بـ 05 تُحوّل إلى +966.',
              ),
              if (commercialWhatsAppPhone(_phone.text) case final phone?)
                Text(
                  'الرقم الدولي: +$phone',
                  key: const ValueKey('international-phone-preview'),
                  textDirection: TextDirection.ltr,
                ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _message,
                minLines: 4,
                maxLines: 8,
                decoration: const InputDecoration(labelText: 'الرسالة'),
                validator: (value) => value == null || value.trim().isEmpty
                    ? 'اكتب الرسالة أولًا'
                    : null,
              ),
              const SizedBox(height: 12),
              const Text(
                'يمكنك تعديل الرقم والرسالة. سيتم فتح واتساب، والإرسال يتم منك داخل واتساب.',
              ),
            ],
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
            Navigator.pop(
              context,
              Uri.parse(
                'https://wa.me/${commercialWhatsAppPhone(_phone.text)}?text=${Uri.encodeComponent(_message.text.trim())}',
              ),
            );
          },
          child: const Text('فتح واتساب'),
        ),
      ],
    ),
  );
}
