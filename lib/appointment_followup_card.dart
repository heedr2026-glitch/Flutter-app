import 'package:flutter/material.dart';
import 'khdoom_dark_page.dart';

class AppointmentFollowupCard extends StatelessWidget {
  final Map<String, dynamic> item;
  final bool canReply;
  final Future<void> Function(String message, int throughId) onReply;
  const AppointmentFollowupCard({super.key, required this.item, required this.canReply, required this.onReply});

  @override
  Widget build(BuildContext context) {
    final messages = List<dynamic>.from(item['followups'] as List? ?? []);
    if (messages.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: const Color(0xFF263450), borderRadius: BorderRadius.circular(12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text('متابعة من العميل (${item['followup_count'] ?? messages.length})', style: const TextStyle(color: Color(0xFFFBBF24), fontWeight: FontWeight.bold)),
        for (final message in messages)
          Padding(padding: const EdgeInsets.only(top: 6), child: Text(message['message'].toString(), style: const TextStyle(color: Colors.white))),
        if (canReply) TextButton.icon(
          icon: const Icon(Icons.reply), label: const Text('الرد على العميل'),
          onPressed: () => showDialog<void>(context: context, barrierDismissible: false,
            builder: (_) => _ReplyDialog(onSend: (text) => onReply(text, (item['followup_latest_id'] as num).toInt()))),
        ),
      ]),
    );
  }
}

class _ReplyDialog extends StatefulWidget {
  final Future<void> Function(String) onSend;
  const _ReplyDialog({required this.onSend});
  @override
  State<_ReplyDialog> createState() => _ReplyDialogState();
}

class _ReplyDialogState extends State<_ReplyDialog> {
  final controller = TextEditingController();
  bool busy = false;
  String? error;
  @override
  void dispose() { controller.dispose(); super.dispose(); }
  Future<void> send() async {
    if (busy || controller.text.trim().isEmpty) return;
    setState(() { busy = true; error = null; });
    try {
      await widget.onSend(controller.text.trim());
      if (mounted) Navigator.pop(context);
    } catch (_) {
      if (mounted) setState(() { busy = false; error = 'تعذر إرسال الرد؛ النص محفوظ هنا، حاول مرة أخرى.'; });
    }
  }
  @override
  Widget build(BuildContext context) => KhdoomDarkPage(child: PopScope(
    canPop: !busy,
    child: AlertDialog(
      title: const Text('الرد على متابعة العميل'),
      content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        const Text('يصل الرد إلى نفس محادثة العميل. تغيير وقت الموعد يتم من زر تعديل الموعد، وليس من نص الرد فقط.'),
        TextField(controller: controller, enabled: !busy, maxLength: 1000, minLines: 2, maxLines: 5,
          decoration: InputDecoration(labelText: 'رد الموظف', errorText: error)),
      ])),
      actions: [
        TextButton(onPressed: busy ? null : () => Navigator.pop(context), child: const Text('إلغاء')),
        FilledButton(onPressed: busy ? null : send, child: Text(busy ? 'جارٍ الإرسال…' : 'إرسال الرد')),
      ],
    ),
  ));
}
