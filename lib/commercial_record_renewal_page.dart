import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

/// A safe renewal assistant: it prepares the owner, but never signs in or
/// submits a government request on the owner's behalf.
class CommercialRecordRenewalPage extends StatefulWidget {
  const CommercialRecordRenewalPage({
    super.key,
    this.platformTitle = 'السجل التجاري',
    this.website = 'https://business.sa',
  });

  final String platformTitle;
  final String website;

  @override
  State<CommercialRecordRenewalPage> createState() =>
      _CommercialRecordRenewalPageState();
}

class _CommercialRecordRenewalPageState
    extends State<CommercialRecordRenewalPage> {
  String get _prefix =>
      'platform_renewal_${base64UrlEncode(utf8.encode(widget.platformTitle))}';
  final _number = TextEditingController();
  DateTime? _dueDate;
  bool _approved = false;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _approved = prefs.getBool('${_prefix}_approval') ?? false;
      _number.text = prefs.getString('${_prefix}_number') ?? '';
      final raw = prefs.getString('${_prefix}_due_date');
      _dueDate = raw == null ? null : DateTime.tryParse(raw);
      _ready = true;
    });
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('${_prefix}_number', _number.text.trim());
    if (_dueDate == null) {
      await prefs.remove('${_prefix}_due_date');
    } else {
      await prefs.setString(
        '${_prefix}_due_date',
        _dueDate!.toIso8601String(),
      );
    }
  }

  Future<void> _approve(bool value) async {
    if (value) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('موافقة صاحب المؤسسة'),
          content: const Text(
            'أوافق على تجهيز طلب التأكيد السنوي. سأراجع البيانات وأكمل الدخول والتحقق والدفع بنفسي في منصة الأعمال.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('أوافق'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('${_prefix}_approval', value);
    if (mounted) setState(() => _approved = value);
  }

  Future<void> _openBusiness() async {
    if (!_approved) return;
    await _save();
    final uri = Uri.parse(widget.website);
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication) &&
        mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('تعذر فتح منصة الأعمال')),
      );
    }
  }

  String _dueLabel() {
    if (_dueDate == null) return 'لم يُحدد موعد التأكيد بعد';
    final date = _dueDate!;
    return 'موعد التأكيد: ${date.year}/${date.month.toString().padLeft(2, '0')}/${date.day.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: Text('التجديد الذكي — ${widget.platformTitle}'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Card(
              color: const Color(0xFF172554),
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.auto_awesome, color: Color(0xFF38BDF8), size: 34),
                    const SizedBox(height: 10),
                    Text(
                      'مساعد تجديد ${widget.platformTitle}',
                      style: TextStyle(color: Colors.white, fontSize: 21, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'يفحص الموعد ويجهزك للطلب. لا يحفظ بيانات النفاذ ولا ينفذ التجديد دون حضور صاحب المؤسسة.',
                      style: TextStyle(color: Colors.white70, height: 1.5),
                    ),
                    const SizedBox(height: 18),
                    TextField(
                      controller: _number,
                      keyboardType: TextInputType.number,
                      style: const TextStyle(color: Colors.white),
                      decoration: const InputDecoration(
                        labelText: 'رقم السجل التجاري',
                        labelStyle: TextStyle(color: Colors.white70),
                        prefixIcon: Icon(Icons.badge_outlined, color: Colors.white70),
                      ),
                    ),
                    const SizedBox(height: 12),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.event, color: Colors.amber),
                      title: Text(_dueLabel(), style: const TextStyle(color: Colors.white)),
                      subtitle: const Text('يُستخدم للتنبيهات فقط', style: TextStyle(color: Colors.white60)),
                      onTap: () async {
                        final picked = await showDatePicker(
                          context: context,
                          initialDate: _dueDate ?? DateTime.now(),
                          firstDate: DateTime(2020),
                          lastDate: DateTime(2040),
                          helpText: 'اختر موعد التأكيد السنوي',
                        );
                        if (picked != null && mounted) {
                          setState(() => _dueDate = picked);
                          await _save();
                        }
                      },
                    ),
                  ],
                ),
              ),
            ),
            Card(
              color: const Color(0xFF13213F),
              child: SwitchListTile(
                value: _approved,
                onChanged: _approve,
                activeColor: const Color(0xFF38BDF8),
                title: const Text('موافقة صاحب المؤسسة', style: TextStyle(color: Colors.white)),
                subtitle: Text(
                  _approved ? 'تمت الموافقة — يمكنك بدء الطلب' : 'الموافقة مطلوبة قبل فتح الطلب',
                  style: const TextStyle(color: Colors.white70),
                ),
              ),
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: _approved ? _openBusiness : null,
              icon: const Icon(Icons.open_in_new),
              label: const Text('بدء التأكيد في منصة الأعمال'),
            ),
            const SizedBox(height: 12),
            const Card(
              color: Color(0xFF13213F),
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  'بعد فتح المنصة: سجّل الدخول بالنفاذ الوطني، راجع بيانات السجل، وافق على الإقرار، ثم أكمل رمز التحقق والدفع بنفسك.',
                  style: TextStyle(color: Colors.white70, height: 1.5),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _number.dispose();
    super.dispose();
  }
}
