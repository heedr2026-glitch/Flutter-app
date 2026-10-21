import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

/// «تواصل مع الدعم» من صفحة الدخول: لمن لا يستطيع الدخول للتطبيق.
/// يرسل الطلب لإدارة خدوم ويعرض رقم الطلب، بدون أي بيانات حساب.
Future<void> showLoginSupportDialog(BuildContext context) async {
  const categories = [
    'نسيت اسم المستخدم',
    'نسيت كلمة المرور',
    'ما أقدر أدخل',
    'مشكلة في التسجيل',
    'غيره',
  ];
  final name = TextEditingController();
  final phone = TextEditingController();
  final message = TextEditingController();
  var category = categories.first;
  var image = '';
  String? error;
  var sending = false;
  String? reference;

  await showDialog<void>(
    context: context,
    builder: (dialogContext) => StatefulBuilder(
      builder: (dialogContext, setState) {
        Future<void> send() async {
          if (name.text.trim().length < 2) {
            setState(() => error = 'اكتب اسمك');
            return;
          }
          if (phone.text.replaceAll(RegExp(r'\D'), '').length < 9) {
            setState(() => error = 'اكتب رقم جوالك عشان نتواصل معك');
            return;
          }
          setState(() {
            sending = true;
            error = null;
          });
          final prefs = await BranchPreferences.getInstance();
          final api = KhdoomCloudApi(
            scope: prefs,
            baseUrl: prefs.getString('cloud_api_url') ??
                'https://khdoom-api.onrender.com',
          );
          try {
            final result = await api.loginHelp({
              'name': name.text.trim(),
              'phone': phone.text.trim(),
              'category': category,
              'message': message.text.trim(),
              if (image.isNotEmpty) 'image': image,
            });
            setState(() => reference = result['reference']?.toString() ?? '');
          } on CloudApiException catch (e) {
            setState(() => error = e.message);
          } catch (_) {
            setState(() => error =
                'تعذر الاتصال بالخادم. تأكد من الإنترنت وحاول مرة ثانية.');
          } finally {
            api.close();
            if (dialogContext.mounted) setState(() => sending = false);
          }
        }

        if (reference != null) {
          return AlertDialog(
            title: const Text('وصل طلبك ✅'),
            content: Text(
              'رقم طلبك: $reference\n\nبنتواصل معك على جوالك في أقرب وقت.',
            ),
            actions: [
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: const Text('تمام'),
              ),
            ],
          );
        }
        return AlertDialog(
          title: const Text('تواصل مع الدعم'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextField(
                  controller: name,
                  decoration: const InputDecoration(labelText: 'اسمك'),
                ),
                TextField(
                  controller: phone,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(labelText: 'رقم جوالك'),
                ),
                const SizedBox(height: 8),
                DropdownButtonFormField<String>(
                  initialValue: category,
                  decoration: const InputDecoration(labelText: 'نوع المشكلة'),
                  items: categories
                      .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                      .toList(),
                  onChanged: (v) => setState(() => category = v ?? category),
                ),
                TextField(
                  controller: message,
                  maxLines: 3,
                  maxLength: 1000,
                  decoration: const InputDecoration(labelText: 'اشرح المشكلة'),
                ),
                OutlinedButton.icon(
                  onPressed: () async {
                    final picked = await ImagePicker().pickImage(
                      source: ImageSource.gallery,
                      imageQuality: 60,
                      maxWidth: 1280,
                      maxHeight: 1280,
                    );
                    if (picked == null) return;
                    final bytes = await picked.readAsBytes();
                    if (bytes.length > 600000) {
                      setState(() => error = 'الصورة كبيرة؛ اختر صورة أصغر');
                      return;
                    }
                    setState(() {
                      image = 'data:image/jpeg;base64,${base64Encode(bytes)}';
                      error = null;
                    });
                  },
                  icon: Icon(
                    image.isEmpty
                        ? Icons.add_photo_alternate_outlined
                        : Icons.check_circle,
                  ),
                  label: Text(image.isEmpty ? 'إرفاق صورة (اختياري)' : 'تم إرفاق الصورة'),
                ),
                if (error != null) ...[
                  const SizedBox(height: 8),
                  Text(error!, style: const TextStyle(color: Colors.redAccent)),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: sending ? null : () => Navigator.pop(dialogContext),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: sending ? null : send,
              child: Text(sending ? 'جاري الإرسال…' : 'إرسال'),
            ),
          ],
        );
      },
    ),
  );
  name.dispose();
  phone.dispose();
  message.dispose();
}
