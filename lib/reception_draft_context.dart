import 'commercial_research/commercial_whatsapp.dart';

/// State for the reception preview only; never submits a real handoff.
class ReceptionDraftContext {
  bool wantsHuman = false;
  String? name;
  String? phone;
  String? _expected;

  String _normalize(String text) => text
      .replaceAll(RegExp('[أإآ]'), 'ا')
      .replaceAll('ؤ', 'و')
      .replaceAll('ى', 'ي')
      .replaceAll('ة', 'ه');

  void observe(String message, {String previousReply = ''}) {
    final text = _normalize(message.trim());
    final human = RegExp(
      r'مس[وؤ]?ول|مدير|موظف بشري|اكلم موظف|ابي موظف|ابغى موظف',
    ).hasMatch(text);
    if (human && !RegExp(r'ما ابي|لا اريد|لا تحول').hasMatch(text)) {
      wantsHuman = true;
    }
    if (RegExp(r'^(نعم|اي|ايه|تمام)$').hasMatch(text) &&
        previousReply.contains('تبي تتواصل مع المسؤول'))
      wantsHuman = true;
    final normalizedPhone = commercialWhatsAppPhone(message);
    final phoneInput = message.trim().replaceAll('٠', '0').replaceAll('٥', '5');
    if (normalizedPhone != null &&
        (phoneInput.startsWith('+') ||
            phoneInput.startsWith('00') ||
            phoneInput.startsWith('05')))
      phone = normalizedPhone;
    final explicitName = RegExp(r'^(?:اسمي|انا اسمي)\s+(.+)$').firstMatch(text);
    final expectingName =
        _expected == 'name' || _normalize(previousReply).contains('اسم');
    final plainName = RegExp(r'^[\u0621-\u064A]+(?: [\u0621-\u064A]+){0,3}$')
        .hasMatch(message.trim());
    if (explicitName != null) {
      name = message
          .trim()
          .substring(message.trim().indexOf(' ') + 1)
          .replaceFirst(RegExp(r'^اسمي\s+'), '');
    } else if (expectingName &&
        plainName &&
        !human &&
        !RegExp(r'سلام|مرحبا|شكرا|ابي|ابغي|اريد|دوام|سعر|^نعم$|^لا$')
            .hasMatch(text)) {
      name = message.trim();
    }
  }

  String reply(String message) {
    if (wantsHuman) {
      if (name == null) {
        _expected = 'name';
        return 'فهمت، تبي تتكلم مع المسؤول. وش اسمك؟';
      }
      if (phone == null) {
        _expected = 'phone';
        return 'تمام، وش رقم التواصل مع رمز الدولة؟ مثل +9665XXXXXXXX.';
      }
      _expected = null;
      return 'الاسم: $name، ورقم التواصل: +$phone. هذه محادثة تجريبية؛ لم يُرسل طلب للمسؤول.';
    }
    final text = _normalize(message);
    if (text.contains('دوام') || text.contains('وقت')) {
      return 'ما عندي ساعات عمل مؤكدة الآن. تبي تتواصل مع المسؤول؟';
    }
    if (text.contains('سعر') || text.contains('كم')) {
      return 'ما عندي سعر معتمد لهذا الطلب الآن. تبي تتواصل مع المسؤول؟';
    }
    if (text.contains('سلام') || text.contains('مرحبا')) {
      return 'حياك الله، وش نقدر نخدمك فيه؟';
    }
    return 'خدمة الرد الذكي غير متاحة الآن، وما أقدر أجاوب طلبك بدقة. تقدر تعيد المحاولة لاحقًا.';
  }
}
