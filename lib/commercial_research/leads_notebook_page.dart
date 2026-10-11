import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../branch_store.dart';
import '../cloud_api.dart';
import 'commercial_whatsapp.dart';

/// «دفتر الفرص»: كل جهة لها حالة (جديد، تواصلت، رد، يبي سعر، أرسلت العرض، فزت، خسرت)،
/// وتذكير متابعة، وملف الجهة، ورابط عرض سعر ذكي، وبحث تلقائي أسبوعي.
/// الإرسال للعملاء يظل بيد صاحب المؤسسة من الواتساب.
class LeadsNotebookPage extends StatefulWidget {
  const LeadsNotebookPage({super.key});

  @override
  State<LeadsNotebookPage> createState() => _LeadsNotebookPageState();
}

class _LeadsNotebookPageState extends State<LeadsNotebookPage> {
  static const _storage = FlutterSecureStorage();
  static const _statusOrder = [
    'new',
    'contacted',
    'replied',
    'wants_price',
    'offer_sent',
    'won',
    'lost',
  ];
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _leads = [];
  Map<String, String> _statuses = const {};
  Map<String, dynamic> _watch = const {};
  final _watchKeywords = TextEditingController();
  final _watchCity = TextEditingController();
  bool _watchEnabled = false;
  String _watchType = 'clients';
  bool _savingWatch = false;
  final Set<String> _busy = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _watchKeywords.dispose();
    _watchCity.dispose();
    super.dispose();
  }

  Future<KhdoomCloudApi?> _api() async {
    final token = await _storage.read(key: 'cloud_session_token');
    if (token == null || token.isEmpty) return null;
    final prefs = await BranchPreferences.getInstance();
    return KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
  }

  void _say(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final api = await _api();
    if (api == null) {
      setState(() {
        _loading = false;
        _error = 'سجّل دخولك بحساب المؤسسة عشان تشوف دفتر الفرص.';
      });
      return;
    }
    try {
      final data = await api.leads();
      final watch = await api.commercialWatch();
      if (!mounted) return;
      setState(() {
        _leads = List<dynamic>.from(data['items'] as List? ?? [])
            .map((item) => Map<String, dynamic>.from(item as Map))
            .toList();
        _statuses = Map<String, dynamic>.from(
          data['statuses'] as Map? ?? {},
        ).map((key, value) => MapEntry(key, value.toString()));
        _watch = watch;
        _watchEnabled = watch['enabled'] == true;
        _watchType = watch['watch_type']?.toString() == 'projects'
            ? 'projects'
            : 'clients';
        _watchKeywords.text = watch['keywords']?.toString() ?? '';
        _watchCity.text = watch['city']?.toString() ?? '';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    } finally {
      api.close();
    }
  }

  Future<void> _saveWatch() async {
    setState(() => _savingWatch = true);
    final api = await _api();
    try {
      if (api == null) throw StateError('سجّل دخولك من جديد');
      final saved = await api.saveCommercialWatch({
        'enabled': _watchEnabled,
        'keywords': _watchKeywords.text.trim(),
        'city': _watchCity.text.trim(),
        'watch_type': _watchType,
      });
      if (mounted) setState(() => _watch = saved);
      _say(
        _watchEnabled
            ? 'تم ✓ بيبحث لك كل أسبوع ويرسل لك إشعار بالفرص الجديدة'
            : 'تم إيقاف البحث الأسبوعي',
      );
    } catch (e) {
      _say(e.toString());
    } finally {
      api?.close();
      if (mounted) setState(() => _savingWatch = false);
    }
  }

  Future<void> _setStatus(Map<String, dynamic> lead, String status) async {
    final id = lead['id'].toString();
    setState(() => _busy.add(id));
    final api = await _api();
    try {
      if (api == null) throw StateError('سجّل دخولك من جديد');
      final result = await api.updateLead(lead['id'], {'status': status});
      if (!mounted) return;
      setState(() {
        lead['status'] = status;
        lead['next_follow_up'] = result['next_follow_up'];
      });
      if (status == 'contacted' || status == 'offer_sent') {
        _say('بذكّرك تتابع معهم بعد 3 أيام إذا ما ردوا 🔔');
      }
    } catch (e) {
      _say(e.toString());
    } finally {
      api?.close();
      if (mounted) setState(() => _busy.remove(id));
    }
  }

  String _messageFor(Map<String, dynamic> lead) {
    final name = lead['name']?.toString() ?? '';
    switch (lead['status']) {
      case 'contacted':
      case 'offer_sent':
        return 'السلام عليكم، نتابع معكم بخصوص تواصلنا السابق مع $name. هل عندكم أي استفسار أو تحتاجون تفاصيل إضافية؟ يسعدنا نخدمكم.';
      case 'wants_price':
        return 'السلام عليكم، عشان نجهز لكم عرض سعر دقيق لـ $name، نرجو تزويدنا بالمخططات أو المقاسات والتفاصيل.';
      default:
        return 'السلام عليكم، معكم مؤسستنا، ويسعدنا نقدم خدماتنا لـ $name. نرجو تزويدنا بتفاصيل احتياجكم لنجهز لكم العرض المناسب.';
    }
  }

  Future<void> _whatsApp(Map<String, dynamic> lead) async {
    await showCommercialWhatsApp(
      context,
      projectName: lead['name']?.toString() ?? '',
      phone: lead['phone']?.toString() ?? '',
      message: _messageFor(lead),
    );
    if (!mounted) return;
    if (lead['status'] == 'new') {
      final sent = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('أرسلت الرسالة؟'),
          content: const Text('إذا أرسلتها نغيّر الحالة إلى «تواصلت» ونذكّرك بالمتابعة.'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('لا'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('إيه أرسلتها'),
            ),
          ],
        ),
      );
      if (sent == true) await _setStatus(lead, 'contacted');
    }
  }

  Future<void> _profile(Map<String, dynamic> lead) async {
    var text = lead['profile']?.toString() ?? '';
    if (text.isEmpty) {
      final id = lead['id'].toString();
      setState(() => _busy.add(id));
      final api = await _api();
      try {
        if (api == null) throw StateError('سجّل دخولك من جديد');
        text = await api.leadProfile(lead['id']);
        if (mounted) setState(() => lead['profile'] = text);
      } catch (e) {
        _say(e.toString());
        return;
      } finally {
        api?.close();
        if (mounted) setState(() => _busy.remove(id));
      }
    }
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('ملف ${lead['name'] ?? 'الجهة'}'),
        content: SingleChildScrollView(child: Text(text)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('تمام'),
          ),
        ],
      ),
    );
  }

  Future<void> _quote(Map<String, dynamic> lead) async {
    final rows = <List<TextEditingController>>[
      [TextEditingController(), TextEditingController(text: '1'), TextEditingController()],
    ];
    final notes = TextEditingController();
    final days = TextEditingController(text: '30');
    final created = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, setDialogState) => AlertDialog(
          title: const Text('رابط عرض سعر'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final row in rows)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Row(
                      children: [
                        Expanded(
                          flex: 3,
                          child: TextField(
                            controller: row[0],
                            decoration: const InputDecoration(labelText: 'البند'),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: TextField(
                            controller: row[1],
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'الكمية'),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          flex: 2,
                          child: TextField(
                            controller: row[2],
                            keyboardType: TextInputType.number,
                            decoration: const InputDecoration(labelText: 'السعر'),
                          ),
                        ),
                      ],
                    ),
                  ),
                TextButton.icon(
                  onPressed: () => setDialogState(
                    () => rows.add([
                      TextEditingController(),
                      TextEditingController(text: '1'),
                      TextEditingController(),
                    ]),
                  ),
                  icon: const Icon(Icons.add),
                  label: const Text('إضافة بند'),
                ),
                TextField(
                  controller: notes,
                  maxLines: 2,
                  decoration: const InputDecoration(
                    labelText: 'ملاحظات (مدة التنفيذ، الضمان…)',
                  ),
                ),
                TextField(
                  controller: days,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'صلاحية العرض بالأيام',
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'العميل يفتح الرابط بدون تطبيق، ويقدر يضغط «موافق» أو «عندي سؤال»، ويوصلك إشعار.',
                  style: TextStyle(fontSize: 12),
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
                final items = [
                  for (final row in rows)
                    if (row[0].text.trim().isNotEmpty)
                      {
                        'title': row[0].text.trim(),
                        'qty': double.tryParse(row[1].text.trim()) ?? 1,
                        'price': double.tryParse(row[2].text.trim()) ?? 0,
                      },
                ];
                final api = await _api();
                try {
                  if (api == null) throw StateError('سجّل دخولك من جديد');
                  final result = await api.createQuote({
                    'leadId': lead['id'],
                    'items': items,
                    'notes': notes.text.trim(),
                    'validDays': int.tryParse(days.text.trim()) ?? 30,
                  });
                  if (dialogContext.mounted) {
                    Navigator.pop(dialogContext, result);
                  }
                } catch (e) {
                  _say(e.toString());
                } finally {
                  api?.close();
                }
              },
              child: const Text('جهّز الرابط'),
            ),
          ],
        ),
      ),
    );
    for (final row in rows) {
      for (final controller in row) {
        controller.dispose();
      }
    }
    notes.dispose();
    days.dispose();
    if (created == null || !mounted) return;
    setState(() => lead['status'] = 'offer_sent');
    await showCommercialWhatsApp(
      context,
      projectName: lead['name']?.toString() ?? '',
      phone: lead['phone']?.toString() ?? '',
      message:
          'السلام عليكم، هذا عرض السعر لـ ${lead['name']} بإجمالي ${created['total']} ريال. تقدرون تشوفونه وتوافقون عليه من الرابط: ${created['url']}',
    );
    await _load();
  }

  Future<void> _delete(Map<String, dynamic> lead) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('حذف الفرصة؟'),
        content: Text('بنحذف «${lead['name']}» من الدفتر.'),
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
    );
    if (confirmed != true) return;
    final api = await _api();
    try {
      if (api == null) throw StateError('سجّل دخولك من جديد');
      await api.deleteLead(lead['id']);
      if (mounted) setState(() => _leads.remove(lead));
    } catch (e) {
      _say(e.toString());
    } finally {
      api?.close();
    }
  }

  Widget _watchCard() {
    final last = _watch['last_run_at']?.toString() ?? '';
    return Card(
      color: const Color(0xFF172554),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: _watchEnabled,
              onChanged: (value) => setState(() => _watchEnabled = value),
              title: const Text(
                '🔎 بحث تلقائي كل أسبوع',
                style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
              ),
              subtitle: const Text(
                'يدور لك فرص جديدة بنفسه ويرسل لك إشعار، بدون ما يكرر اللي عندك',
                style: TextStyle(color: Colors.white70),
              ),
            ),
            Wrap(
              spacing: 8,
              children: [
                ChoiceChip(
                  label: const Text('عملاء محتملين'),
                  selected: _watchType == 'clients',
                  onSelected: (_) => setState(() => _watchType = 'clients'),
                ),
                ChoiceChip(
                  label: const Text('مشاريع جديدة'),
                  selected: _watchType == 'projects',
                  onSelected: (_) => setState(() => _watchType = 'projects'),
                ),
              ],
            ),
            TextField(
              controller: _watchKeywords,
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(
                labelText: 'وش تدور؟ (مثل: مقاولين، عمائر تحت الإنشاء)',
                labelStyle: TextStyle(color: Colors.white70),
              ),
            ),
            TextField(
              controller: _watchCity,
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(
                labelText: 'المدينة',
                labelStyle: TextStyle(color: Colors.white70),
              ),
            ),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: _savingWatch ? null : _saveWatch,
              child: Text(_savingWatch ? 'جاري الحفظ…' : 'حفظ'),
            ),
            if (last.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'آخر بحث: ${last.length >= 10 ? last.substring(0, 10) : last} — ${_watch['last_result'] ?? ''}',
                  style: const TextStyle(color: Colors.white54, fontSize: 12),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _leadCard(Map<String, dynamic> lead) {
    final id = lead['id'].toString();
    final status = lead['status']?.toString() ?? 'new';
    final quote = lead['quote'] is Map
        ? Map<String, dynamic>.from(lead['quote'] as Map)
        : null;
    final details = [
      lead['activity'],
      lead['location'],
    ].where((value) => (value ?? '').toString().isNotEmpty).join(' · ');
    return Card(
      color: const Color(0xFF111B35),
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    lead['name']?.toString() ?? '',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                if (lead['origin'].toString().startsWith('weekly'))
                  const Padding(
                    padding: EdgeInsets.only(left: 6),
                    child: Text('🔎', style: TextStyle(fontSize: 16)),
                  ),
                IconButton(
                  tooltip: 'حذف',
                  onPressed: () => _delete(lead),
                  icon: const Icon(Icons.delete_outline, color: Colors.white38),
                ),
              ],
            ),
            if (details.isNotEmpty)
              Text(details, style: const TextStyle(color: Colors.white60)),
            if ((lead['details'] ?? '').toString().isNotEmpty)
              Text(
                lead['details'].toString(),
                style: const TextStyle(color: Colors.white70),
              ),
            if ((lead['phone'] ?? '').toString().isNotEmpty)
              Text(
                '📞 ${lead['phone']}',
                style: const TextStyle(color: Color(0xFF86EFAC)),
              ),
            if (quote != null)
              Text(
                quote['status'] == 'accepted'
                    ? '✅ وافق على عرض السعر (${quote['total']} ريال)'
                    : quote['status'] == 'question'
                    ? '❓ عنده سؤال عن العرض'
                    : '📄 عرض السعر: فتحه ${quote['open_count'] ?? 0} مرة',
                style: const TextStyle(color: Color(0xFFFBBF24)),
              ),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                DropdownButton<String>(
                  value: _statusOrder.contains(status) ? status : 'new',
                  dropdownColor: const Color(0xFF172554),
                  style: const TextStyle(color: Colors.white),
                  onChanged: _busy.contains(id)
                      ? null
                      : (value) {
                          if (value != null && value != status) {
                            _setStatus(lead, value);
                          }
                        },
                  items: [
                    for (final key in _statusOrder)
                      DropdownMenuItem(
                        value: key,
                        child: Text(_statuses[key] ?? key),
                      ),
                  ],
                ),
                FilledButton.icon(
                  onPressed: () => _whatsApp(lead),
                  icon: const Icon(Icons.chat, size: 18),
                  label: const Text('واتساب'),
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF15803D),
                  ),
                ),
                OutlinedButton(
                  onPressed: _busy.contains(id) ? null : () => _profile(lead),
                  child: Text(
                    _busy.contains(id) ? 'جاري…' : 'ملف الجهة',
                  ),
                ),
                OutlinedButton(
                  onPressed: () => _quote(lead),
                  child: const Text('رابط عرض سعر'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final open = _leads.where((l) => l['status'] != 'won' && l['status'] != 'lost').length;
    final won = _leads.where((l) => l['status'] == 'won').length;
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          title: const Text('دفتر الفرص'),
          actions: [
            IconButton(
              tooltip: 'تحديث',
              onPressed: _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.all(14),
                  children: [
                    if (_error != null)
                      Text(_error!, style: const TextStyle(color: Colors.redAccent)),
                    Text(
                      'فرص مفتوحة: $open · فزت: $won',
                      style: const TextStyle(color: Colors.white70),
                    ),
                    const SizedBox(height: 10),
                    _watchCard(),
                    const SizedBox(height: 12),
                    if (_leads.isEmpty)
                      const Text(
                        'الدفتر فاضي. احفظ الجهات من نتائج البحث التجاري بزر «احفظ كفرصة»، أو شغّل البحث الأسبوعي.',
                        style: TextStyle(color: Colors.white60),
                      )
                    else
                      ..._leads.map(_leadCard),
                  ],
                ),
              ),
      ),
    );
  }
}
