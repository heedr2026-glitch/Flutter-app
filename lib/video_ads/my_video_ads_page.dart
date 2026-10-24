import 'package:flutter/material.dart';

import 'create_video_ad_page.dart';
import '../khdoom_dark_page.dart';
import 'video_ads_api.dart';

/// «المكتبة»: فيديوهات المؤسسة بكل حالاتها، مع إعادة النشر والحذف.
class MyVideoAdsPage extends StatefulWidget {
  const MyVideoAdsPage({super.key});

  @override
  State<MyVideoAdsPage> createState() => _MyVideoAdsPageState();
}

class _MyVideoAdsPageState extends State<MyVideoAdsPage> {
  // الحوارات تاخذ ألوان الصفحة الداكنة من هنا.
  final _darkKey = GlobalKey();
  VideoAdsApi? _api;
  List<Map<String, dynamic>> _items = [];
  Map<String, dynamic> _quota = {};
  bool _loading = true;
  String? _error;

  static const _status = {
    'draft': ('مسودة', Colors.grey),
    'pending': ('بانتظار الموافقة ⏳', Colors.orange),
    'published': ('منشور ✅', Colors.green),
    'rejected': ('مرفوض', Colors.red),
    'expired': ('منتهي', Colors.blueGrey),
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = _api ??= await VideoAdsApi.create();
      final data = await api.mine();
      if (!mounted) return;
      setState(() {
        _items = [
          for (final x in (data['items'] as List? ?? const []))
            Map<String, dynamic>.from(x as Map),
        ];
        _quota = Map<String, dynamic>.from(data['quota'] as Map? ?? const {});
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.toString();
        _loading = false;
      });
    }
  }

  Future<void> _delete(Map<String, dynamic> item) async {
    final status = item['status'];
    final ok = await showDialog<bool>(
      context: _darkKey.currentContext ?? context,
      builder: (context) => Directionality(
        textDirection: TextDirection.rtl,
        child: AlertDialog(
          title: const Text('حذف الفيديو'),
          content: Text(
            status == 'published'
                ? 'الإعلان منشور الحين، بينشال من المشاهدة فورًا وينحذف نهائي. متأكد؟'
                : status == 'pending'
                ? 'بينلغي طلب النشر وينحذف الفيديو نهائي. متأكد؟'
                : 'متأكد تبي تحذف الفيديو نهائي؟',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('إلغاء'),
            ),
            FilledButton(
              style: FilledButton.styleFrom(backgroundColor: Colors.red),
              onPressed: () => Navigator.pop(context, true),
              child: const Text('حذف'),
            ),
          ],
        ),
      ),
    );
    if (ok != true) return;
    try {
      await _api!.delete((item['id'] as num).toInt());
      _message('انحذف الفيديو');
      _load();
    } catch (error) {
      _message(error.toString());
    }
  }

  Future<void> _republish(Map<String, dynamic> item) async {
    final freeLeft =
        _quota['can_create'] == true && ((_quota['remaining'] as num?) ?? 0) > 0;
    var pay = false;
    var transfer = '';
    if (!freeLeft) {
      final controller = TextEditingController();
      final price = ((_quota['paid_price_sar'] as num?) ?? 29).toStringAsFixed(0);
      final name = await showDialog<String>(
        context: _darkKey.currentContext ?? context,
        builder: (context) => Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            title: const Text('إعادة النشر'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  _quota['can_create'] == true
                      ? 'خلصت إعلاناتك المجانية هذا الشهر. إعادة النشر بـ $price ريال.'
                      : 'إعادة النشر بـ $price ريال (أو اشترك VIP ⭐).',
                ),
                TextField(
                  controller: controller,
                  decoration: const InputDecoration(labelText: 'اسم المحوّل'),
                ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, controller.text.trim()),
                child: const Text('إرسال'),
              ),
            ],
          ),
        ),
      );
      controller.dispose();
      if (name == null) return;
      if (name.isEmpty) {
        _message('اكتب اسم المحوّل');
        return;
      }
      pay = true;
      transfer = name;
    }
    try {
      await _api!.submit((item['id'] as num).toInt(), pay: pay, transferName: transfer);
      _message('انرسل للمراجعة ✅');
      _load();
    } catch (error) {
      _message(error.toString());
    }
  }

  void _message(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    return KhdoomDarkPage(
      child: Directionality(
      key: _darkKey,
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(title: const Text('📚 المكتبة')),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: () async {
            final created = await Navigator.push<bool>(
              context,
              MaterialPageRoute(builder: (_) => const CreateVideoAdPage()),
            );
            if (created == true) _load();
          },
          icon: const Icon(Icons.add),
          label: const Text('أضف مقطع'),
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
            ? Center(child: Text(_error!))
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 90),
                  children: [
                    if (_quota['can_create'] == true)
                      Card(
                        child: ListTile(
                          leading: const Icon(Icons.campaign),
                          title: Text(
                            'باقي لك ${_quota['remaining']} من ${_quota['limit']} إعلانات هذا الشهر',
                          ),
                          subtitle: Text(
                            'تقدر تحتفظ بـ ${_quota['max_saved']} فيديوهات، الزائد يحذف الأقدم',
                          ),
                        ),
                      ),
                    if (_items.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(32),
                        child: Text(
                          'ما عندك إعلانات فيديو بعد',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    for (final item in _items) _card(item),
                  ],
                ),
              ),
      ),
    ),
    );
  }

  Widget _card(Map<String, dynamic> item) {
    final status = _status[item['status']] ?? ('—', Colors.grey);
    final canRepublish = const {'draft', 'expired', 'rejected'}.contains(item['status']);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.movie, size: 28),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    (item['title'] ?? '').toString().isEmpty
                        ? (item['overlayText'] ?? 'بدون عنوان').toString()
                        : item['title'].toString(),
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                ),
                Chip(
                  label: Text(status.$1, style: const TextStyle(color: Colors.white)),
                  backgroundColor: status.$2,
                ),
              ],
            ),
            if (item['status'] == 'rejected' && (item['reviewNote'] ?? '').toString().isNotEmpty)
              Text(
                'سبب الرفض: ${item['reviewNote']}',
                style: const TextStyle(color: Colors.red),
              ),
            Text('❤️ ${(item['likes'] as num?)?.toInt() ?? 0} إعجاب'),
            if (item['status'] == 'published' && item['expiresAt'] != null)
              Text('ينتهي: ${_date(item['expiresAt'])}'),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                if (canRepublish)
                  TextButton.icon(
                    onPressed: () => _republish(item),
                    icon: const Icon(Icons.replay),
                    label: const Text('إعادة نشر'),
                  ),
                TextButton.icon(
                  onPressed: () => _delete(item),
                  icon: const Icon(Icons.delete_outline, color: Colors.red),
                  label: const Text('حذف', style: TextStyle(color: Colors.red)),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  String _date(Object? raw) {
    final d = DateTime.tryParse(raw.toString())?.toLocal();
    if (d == null) return '';
    final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
    return '${d.year}/${d.month}/${d.day} $h:${d.minute.toString().padLeft(2, '0')} ${d.hour < 12 ? 'ص' : 'م'}';
  }
}
