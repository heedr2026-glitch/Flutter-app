import 'package:flutter/material.dart';

import 'commercial_whatsapp.dart';

/// Retains the complete report, splitting numbered results into contact cards.
List<String> commercialReportSections(String report) {
  final starts = RegExp(
    r'^\s*(?:#{1,6}\s*)?(?:\*\*)?[0-9٠-٩]+[.)\-]\s+\S',
    multiLine: true,
  ).allMatches(report).map((match) => match.start).toList();
  if (starts.isEmpty) return [report];
  final boundaries = {0, ...starts, report.length}.toList()..sort();
  return [
    for (var i = 0; i < boundaries.length - 1; i++)
      report.substring(boundaries[i], boundaries[i + 1]).trim(),
  ].where((part) => part.isNotEmpty).toList();
}

String commercialReportPhone(String section) {
  var text = section;
  for (var i = 0; i < 10; i++) {
    text = text.replaceAll('٠١٢٣٤٥٦٧٨٩'[i], '$i');
  }
  // The research screen searches Saudi Arabia. Only infer +966 for Saudi mobiles.
  final match = RegExp(r'(?<![0-9])(?:\+?966|00966|0)5[0-9]{8}(?![0-9])')
      .firstMatch(text);
  if (match == null) return '';
  final phone = match.group(0)!;
  return phone.startsWith('05')
      ? '966${phone.substring(1)}'
      : commercialWhatsAppPhone(phone) ?? '';
}

class CommercialResearchReport extends StatelessWidget {
  const CommercialResearchReport({super.key, required this.text});
  final String text;
  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: commercialReportSections(text).map((section) {
      final title = section
          .split('\n')
          .first
          .replaceAll(RegExp(r'^[#\s*]*[0-9٠-٩]+[.)\-]\s*|[*#]'), '')
          .trim();
      final numbered = RegExp(r'^[#\s*]*[0-9٠-٩]+[.)\-]').hasMatch(section);
      return Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF172554),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              section,
              style: const TextStyle(color: Colors.white70, height: 1.5),
            ),
            if (numbered || commercialReportPhone(section).isNotEmpty) ...[
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: () => showCommercialWhatsApp(
                  context,
                  projectName: title,
                  phone: commercialReportPhone(section),
                ),
                icon: const Icon(Icons.chat),
                label: const Text('تواصل عبر واتساب'),
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFF15803D),
                ),
              ),
            ],
          ],
        ),
      );
    }).toList(),
  );
}
