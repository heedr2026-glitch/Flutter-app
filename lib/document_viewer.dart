import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

Uint8List? documentImageDataBytes(String? value) {
  final data = value?.trim() ?? '';
  if (!data.startsWith('data:image/') || !data.contains(',')) return null;
  try {
    return base64Decode(data.substring(data.indexOf(',') + 1));
  } catch (_) {
    return null;
  }
}

Uri? documentWebsite(String value) {
  final text = value.trim();
  if (text.isEmpty) return null;
  final uri = Uri.tryParse(text.contains('://') ? text : 'https://$text');
  if (uri == null ||
      !['http', 'https'].contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.host.contains(' ') ||
      uri.userInfo.isNotEmpty)
    return null;
  return uri;
}

Future<void> openDocumentWebsite(BuildContext context, String value) async {
  final uri = documentWebsite(value);
  try {
    if (uri != null &&
        await launchUrl(uri, mode: LaunchMode.externalApplication))
      return;
  } catch (_) {
    // Keep navigation failures inside the current screen.
  }
  if (context.mounted) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('تعذر فتح الرابط. تحقق من صحة عنوان الموقع.'),
      ),
    );
  }
}

class DocumentImage extends StatelessWidget {
  const DocumentImage({
    super.key,
    required this.path,
    this.data,
    this.title = 'صورة المستند',
    this.width,
    this.height,
    this.fit = BoxFit.cover,
    this.errorBuilder,
  });

  final String path;
  final String? data;
  final String title;
  final double? width;
  final double? height;
  final BoxFit fit;
  final ImageErrorWidgetBuilder? errorBuilder;

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    label: 'تكبير $title',
    child: InkWell(
      onTap: () => Navigator.of(context).push<void>(
        MaterialPageRoute(
          fullscreenDialog: true,
          builder: (_) =>
              DocumentImagePage(path: path, data: data, title: title),
        ),
      ),
      child: documentImageDataBytes(data) != null
          ? Image.memory(
              documentImageDataBytes(data)!,
              width: width,
              height: height,
              fit: fit,
              errorBuilder: errorBuilder,
            )
          : Image.file(
              File(path),
              width: width,
              height: height,
              fit: fit,
              errorBuilder:
                  errorBuilder ??
                  (_, _, _) => const Icon(
                    Icons.broken_image_outlined,
                    color: Colors.white70,
                  ),
            ),
    ),
  );
}

class DocumentImagePage extends StatelessWidget {
  const DocumentImagePage({
    super.key,
    required this.path,
    this.data,
    required this.title,
  });
  final String path;
  final String? data;
  final String title;

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(title),
        leading: IconButton(
          tooltip: 'إغلاق الصورة',
          icon: const Icon(Icons.close),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: InteractiveViewer(
                minScale: 1,
                maxScale: 8,
                child: SizedBox.expand(
                  child: documentImageDataBytes(data) != null
                      ? Image.memory(
                          documentImageDataBytes(data)!,
                          fit: BoxFit.contain,
                        )
                      : Image.file(
                          File(path),
                          fit: BoxFit.contain,
                          errorBuilder: (_, _, _) => const Center(
                            child: Text(
                              'الصورة غير متاحة على هذا الجهاز',
                              style: TextStyle(color: Colors.white),
                            ),
                          ),
                        ),
                ),
              ),
            ),
            const Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                'استخدم إصبعين للتكبير والتحريك',
                style: TextStyle(color: Colors.white70),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class DocumentImagePicker extends StatelessWidget {
  const DocumentImagePicker({
    super.key,
    required this.path,
    required this.onChanged,
  });
  final String path;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    children: [
      if (path.isNotEmpty)
        DocumentImage(path: path, width: double.infinity, height: 130),
      OutlinedButton.icon(
        icon: const Icon(Icons.add_a_photo_outlined),
        label: Text(path.isEmpty ? 'إضافة صورة' : 'تغيير الصورة'),
        onPressed: () async {
          try {
            final selected = await const MethodChannel('khdoom/profile_image')
                .invokeMethod<String>('pickImage', {
                  'fileName':
                      'khdoom_document_${DateTime.now().microsecondsSinceEpoch}',
                });
            if (context.mounted && selected != null && selected.isNotEmpty) {
              onChanged(selected);
            }
          } on PlatformException {
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('تعذر اختيار الصورة')),
              );
            }
          }
        },
      ),
    ],
  );
}

class RecordAttachments extends StatelessWidget {
  const RecordAttachments({super.key, required this.records});
  final List<Map<String, dynamic>> records;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      for (final record in records) ...[
        if ((record['imagePath']?.toString() ?? '').isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: DocumentImage(
              path: record['imagePath']?.toString() ?? '',
              data: record['imageData']?.toString(),
              title: (record['title'] ?? record['name'] ?? 'صورة المستند')
                  .toString(),
              width: double.infinity,
              height: 150,
              fit: BoxFit.contain,
            ),
          ),
        if ((record['website']?.toString() ?? '').trim().isNotEmpty) ...[
          SelectableText(
            record['website'].toString(),
            style: const TextStyle(color: Color(0xFF7DD3FC)),
          ),
          TextButton.icon(
            onPressed: () =>
                openDocumentWebsite(context, record['website'].toString()),
            icon: const Icon(Icons.open_in_new),
            label: const Text('فتح الموقع'),
          ),
        ],
      ],
    ],
  );
}

class RecordInformationPage extends StatelessWidget {
  const RecordInformationPage({super.key, required this.record});
  final Map<String, dynamic> record;

  @override
  Widget build(BuildContext context) {
    final date = DateTime.tryParse(record['date']?.toString() ?? '');
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: Text((record['title'] ?? 'معلومات المستند').toString()),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
        ),
        body: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            SelectableText(
              (record['details']?.toString().trim().isNotEmpty ?? false)
                  ? record['details'].toString()
                  : 'لا توجد ملاحظات إضافية',
              style: const TextStyle(color: Colors.white, height: 1.6),
            ),
            const SizedBox(height: 12),
            Text(
              date == null
                  ? 'تاريخ الانتهاء غير محدد'
                  : 'تاريخ الانتهاء: ${date.day}/${date.month}/${date.year}',
              style: const TextStyle(color: Colors.white70),
            ),
            RecordAttachments(records: [record]),
          ],
        ),
      ),
    );
  }
}
