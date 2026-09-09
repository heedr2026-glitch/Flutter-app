import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:image_picker/image_picker.dart';

import 'branch_store.dart';
import 'cloud_api.dart';
import 'khdoom_dark_page.dart';

String advertisementStatus(Map<String, dynamic> ad, {DateTime? at}) {
  final end = DateTime.tryParse(ad['expires_at']?.toString() ?? '');
  final status = ad.containsKey('approved') && ad.containsKey('active')
      ? ((ad['approved'] == true || ad['approved'] == 1)
            ? ((ad['active'] == true || ad['active'] == 1)
                  ? 'published'
                  : 'paused')
            : ((ad['active'] == true || ad['active'] == 1)
                  ? 'pending'
                  : 'rejected'))
      : ad['status']?.toString() ?? 'unknown';
  if ((status == 'published' || status == 'paused') &&
      end != null &&
      !end.isAfter(at ?? DateTime.now()))
    return 'expired';
  return status;
}

String advertisementLabel(Map<String, dynamic> ad) =>
    const {
      'pending': 'قيد المراجعة',
      'published': 'منشور',
      'rejected': 'مرفوض',
      'expired': 'انتهى الإعلان',
      'paused': 'متوقف',
    }[advertisementStatus(ad)] ??
    'حالة غير معروفة';

String advertisementRemaining(Map<String, dynamic> ad) {
  final status = advertisementStatus(ad);
  if (status == 'expired') return 'انتهت مدة الإعلان';
  if (status != 'published') return 'يبدأ احتساب المدة بعد موافقة الإدارة';
  final end = DateTime.tryParse(ad['expires_at']?.toString() ?? '');
  if (end == null) return 'عرض بدون تاريخ نهاية — معتمد من الإدارة';
  final days = (end.difference(DateTime.now()).inSeconds / 86400).ceil();
  return 'المتبقي: $days يوم';
}

class MyAdsService {
  static Future<T> withApi<T>(Future<T> Function(KhdoomCloudApi) work) async {
    final prefs = await BranchPreferences.getInstance();
    final token = await const FlutterSecureStorage().read(
      key: 'cloud_session_token',
    );
    if (token == null || token.isEmpty)
      throw const CloudApiException(
        401,
        'سجّل الدخول لعرض إعلانك وإرساله للمراجعة',
      );
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      return await work(api).timeout(const Duration(seconds: 25));
    } finally {
      api.close();
    }
  }

  static Future<List<Map<String, dynamic>>> load() => withApi(
    (api) async => (await api.myAdvertisements())
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList(),
  );
  static Future<void> submit(Map<String, dynamic> request) =>
      withApi((api) async {
        await api.createAdvertisement(
          title: request['title'],
          message: request['message'],
          contact: request['contact'],
          requestedDays: request['requestedDays'],
          imageData: request['imageData']?.toString() ?? '',
        );
      });
}

const advertisementGroups = <String, String>{
  'all': 'الكل',
  'pending': 'قيد المراجعة',
  'published': 'المنشور',
  'expired': 'المنتهي',
  'rejected': 'المرفوض',
  'paused': 'المتوقف',
};
List<Map<String, dynamic>> filterAdvertisements(
  List<Map<String, dynamic>> ads,
  String status,
) => status == 'all'
    ? ads
    : ads.where((ad) => advertisementStatus(ad) == status).toList();

class AdvertisementFilters extends StatelessWidget {
  final List<Map<String, dynamic>> ads;
  final String selected;
  final ValueChanged<String> onSelected;
  const AdvertisementFilters({
    super.key,
    required this.ads,
    required this.selected,
    required this.onSelected,
  });
  @override
  Widget build(BuildContext context) => Wrap(
    spacing: 6,
    runSpacing: 4,
    children: [
      for (final group in advertisementGroups.entries)
        ChoiceChip(
          label: Text(
            '${group.value} (${filterAdvertisements(ads, group.key).length})',
          ),
          selected: selected == group.key,
          onSelected: (_) => onSelected(group.key),
        ),
    ],
  );
}

class VipAdvertisementCard extends StatefulWidget {
  const VipAdvertisementCard({super.key});
  @override
  State<VipAdvertisementCard> createState() => _VipAdvertisementCardState();
}

class _VipAdvertisementCardState extends State<VipAdvertisementCard>
    with WidgetsBindingObserver {
  Map<String, dynamic>? _latest;
  List<Map<String, dynamic>> _ads = [];
  Map<String, dynamic>? _published;
  DateTime? _updatedAt;
  String? _error;
  bool _busy = false;
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final ads = await MyAdsService.load();
      if (mounted)
        setState(() {
          _latest = ads.isEmpty ? null : ads.first;
          _ads = ads;
          final published = ads.where(
            (ad) => advertisementStatus(ad) == 'published',
          );
          _published = published.isEmpty ? null : published.first;
          _updatedAt = DateTime.now();
          _error = null;
        });
    } catch (_) {
      if (mounted)
        setState(
          () => _error = 'تعذر تحديث حالة الإعلان. افتح إعلاني للمحاولة.',
        );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  @override
  Widget build(BuildContext context) => CompactAdvertisementSummary(
    ads: _ads,
    onFilter: (status) async {
      await Navigator.push<void>(
        context,
        MaterialPageRoute(
          builder: (_) => MyAdvertisementsPage(initialStatus: status),
        ),
      );
      if (mounted) await _load();
    },
    latest: _latest,
    published: _published,
    error: _error,
    updatedAt: _updatedAt,
    busy: _busy,
    onRefresh: _load,
    onOpen: () async {
      await Navigator.push<void>(
        context,
        MaterialPageRoute(builder: (_) => const MyAdvertisementsPage()),
      );
      if (mounted) await _load();
    },
  );
}

class CompactAdvertisementSummary extends StatelessWidget {
  final List<Map<String, dynamic>>? ads;
  final ValueChanged<String>? onFilter;
  final Map<String, dynamic>? latest, published;
  final String? error;
  final DateTime? updatedAt;
  final bool busy;
  final VoidCallback onOpen, onRefresh;
  const CompactAdvertisementSummary({
    super.key,
    this.ads,
    this.onFilter,
    this.latest,
    this.published,
    this.error,
    this.updatedAt,
    this.busy = false,
    required this.onOpen,
    required this.onRefresh,
  });
  @override
  Widget build(BuildContext context) {
    final shown = published ?? latest;
    final time = updatedAt == null
        ? ''
        : '${updatedAt!.hour.toString().padLeft(2, '0')}:${updatedAt!.minute.toString().padLeft(2, '0')}';
    return Card(
      color: const Color(0xFF172554),
      child: InkWell(
        onTap: onOpen,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 4, 14, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  const Text(
                    'إعلاني',
                    style: TextStyle(
                      color: Color(0xFFFBBF24),
                      fontSize: 22,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const Spacer(),
                  IconButton(
                    tooltip: 'تحديث حالة الإعلان',
                    onPressed: busy ? null : onRefresh,
                    icon: Icon(
                      busy ? Icons.hourglass_top : Icons.refresh,
                      color: Colors.white70,
                      size: 20,
                    ),
                  ),
                ],
              ),
              if (ads != null && error == null)
                Row(
                  children: [
                    for (final status in ['pending', 'published', 'expired'])
                      Expanded(
                        child: TextButton(
                          onPressed: () => onFilter?.call(status),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                '${filterAdvertisements(ads!, status).length}',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 17,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              Text(
                                advertisementGroups[status]!,
                                maxLines: 1,
                                style: const TextStyle(
                                  color: Colors.white70,
                                  fontSize: 11,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                  ],
                ),
              if (ads == null && shown != null)
                Text(
                  shown['title']?.toString() ?? '',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 16),
                ),
              if (ads == null || error != null)
                Text(
                  error != null
                      ? 'تعذر التحديث — اضغط للمحاولة والتفاصيل'
                      : shown == null
                      ? 'أرسل إعلانك للمراجعة'
                      : '${advertisementLabel(shown)} • طلب #${shown['id']} • ${advertisementStatus(shown) == 'published' ? advertisementRemaining(shown) : shown['title'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: error == null ? Colors.white : Colors.orangeAccent,
                    fontSize: 13,
                  ),
                ),
              if (ads == null &&
                  latest != null &&
                  published != null &&
                  latest!['id'] != published!['id'])
                Text(
                  'آخر طلب #${latest!['id']}: ${advertisementLabel(latest!)}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white70, fontSize: 12),
                ),
              const SizedBox(height: 4),
              Row(
                children: [
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      visualDensity: VisualDensity.compact,
                    ),
                    onPressed: onOpen,
                    icon: const Icon(Icons.campaign, size: 20),
                    label: const Text('فتح إعلاني'),
                  ),
                  const SizedBox(width: 8),
                  if (updatedAt != null)
                    Expanded(
                      child: Text(
                        'آخر تحديث: $time',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.end,
                        style: const TextStyle(
                          color: Colors.white54,
                          fontSize: 11,
                        ),
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class MyAdvertisementsPage extends StatefulWidget {
  final String initialStatus;
  const MyAdvertisementsPage({super.key, this.initialStatus = 'all'});
  @override
  State<MyAdvertisementsPage> createState() => _MyAdvertisementsPageState();
}

class _MyAdvertisementsPageState extends State<MyAdvertisementsPage>
    with WidgetsBindingObserver {
  List<Map<String, dynamic>> _ads = [];
  late String _selectedStatus;
  String? _error;
  bool _loading = true;
  bool _submitting = false;
  bool _fetching = false;
  DateTime? _updatedAt;
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    _selectedStatus = advertisementGroups.containsKey(widget.initialStatus)
        ? widget.initialStatus
        : 'all';
    WidgetsBinding.instance.addObserver(this);
    _load();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  Future<void> _load() async {
    if (_fetching) return;
    _fetching = true;
    try {
      final ads = await MyAdsService.load();
      if (mounted)
        setState(() {
          _ads = ads;
          _updatedAt = DateTime.now();
          _error = null;
        });
    } catch (error) {
      if (mounted)
        setState(
          () => _error = error is CloudApiException
              ? error.message
              : 'تعذر تحديث الإعلانات، حاول مرة أخرى',
        );
    } finally {
      _fetching = false;
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _request([Map<String, dynamic>? previous]) async {
    if (_submitting) return;
    final request = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) =>
          KhdoomDarkPage(child: AdvertisementRequestDialog(initial: previous)),
    );
    if (!mounted || request == null) return;
    setState(() => _submitting = true);
    try {
      await MyAdsService.submit(request);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text(
            'تم إرسال الإعلان للمراجعة. المدة النهائية يحددها المالك.',
          ),
        ),
      );
      await _load();
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              error is CloudApiException ? error.message : 'لم نتأكد من الإرسال. حدّث القائمة قبل إعادة المحاولة لتجنب تكرار الطلب.',
            ),
          ),
        );
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  String _date(dynamic raw) {
    final date = DateTime.tryParse(raw?.toString() ?? '')?.toLocal();
    return date == null
        ? 'لم يحدد بعد'
        : '${date.year}/${date.month}/${date.day} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) => KhdoomDarkPage(
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('إعلاني'),
          actions: [
            IconButton(
              onPressed: _load,
              icon: const Icon(Icons.refresh),
              tooltip: 'تحديث الحالة',
            ),
          ],
        ),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  const Text(
                    'تختار مدة الطلب من 1 إلى 10 أيام. الإدارة تراجع الإعلان وتحدد المدة النهائية، وقد تزيدها أو تقللها.',
                  ),
                  const SizedBox(height: 12),
                  AdvertisementFilters(
                    ads: _ads,
                    selected: _selectedStatus,
                    onSelected: (status) =>
                        setState(() => _selectedStatus = status),
                  ),
                  if (_updatedAt != null)
                    Text(
                      'آخر تحديث للحالة: ${_date(_updatedAt!.toIso8601String())}',
                    ),
                  FilledButton.icon(
                    onPressed: _submitting ? null : () => _request(),
                    icon: const Icon(Icons.add),
                    label: Text(
                      _submitting ? 'جارٍ الإرسال…' : 'طلب إعلان جديد',
                    ),
                  ),
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(
                        _error!,
                        style: const TextStyle(color: Colors.red),
                      ),
                    ),
                  if (_ads.isEmpty && _error == null)
                    const Padding(
                      padding: EdgeInsets.all(20),
                      child: Text('لم ترسل إعلانًا بعد'),
                    ),
                  if (_ads.isNotEmpty &&
                      filterAdvertisements(_ads, _selectedStatus).isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(16),
                      child: Text('لا توجد إعلانات في هذه الحالة'),
                    ),
                  for (final ad in filterAdvertisements(_ads, _selectedStatus))
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              advertisementLabel(ad),
                              style: const TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 20,
                              ),
                            ),
                            Text('رقم طلب الإعلان: #${ad['id']}'),
                            Text('أُرسل في: ${_date(ad['created_at'])}'),
                            Text(advertisementRemaining(ad)),
                            if (ad['requested_days'] != null)
                              Text(
                                'المدة التي طلبتها: ${ad['requested_days']} أيام',
                              ),
                            Text(
                              'بداية العرض المعتمدة: ${_date(ad['approved_at'])}',
                            ),
                            Text(
                              'نهاية العرض: ${ad['approved_at'] != null && ad['expires_at'] == null ? 'بدون نهاية' : _date(ad['expires_at'])}',
                            ),
                            if ((ad['review_note']?.toString() ?? '')
                                .isNotEmpty)
                              Text('ملاحظة الإدارة: ${ad['review_note']}'),
                            const Divider(),
                            const Text('معاينة محتوى الإعلان'),
                            SelectableText(
                              ad['title']?.toString() ?? '',
                              style: const TextStyle(
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            SelectableText(ad['message']?.toString() ?? ''),
                            SelectableText('التواصل: ${ad['contact'] ?? ''}'),
                            if ([
                              'expired',
                              'rejected',
                              'paused',
                            ].contains(advertisementStatus(ad)))
                              TextButton.icon(
                                onPressed: _submitting
                                    ? null
                                    : () => _request(ad),
                                icon: const Icon(Icons.replay),
                                label: Text(
                                  advertisementStatus(ad) == 'expired'
                                      ? 'طلب تجديد الإعلان'
                                      : 'تعديل وإعادة الإرسال للمراجعة',
                                ),
                              ),
                          ],
                        ),
                      ),
                    ),
                ],
              ),
      ),
    ),
  );
}

class AdvertisementRequestDialog extends StatefulWidget {
  final Map<String, dynamic>? initial;
  const AdvertisementRequestDialog({super.key, this.initial});
  @override
  State<AdvertisementRequestDialog> createState() =>
      _AdvertisementRequestDialogState();
}

class _AdvertisementRequestDialogState
    extends State<AdvertisementRequestDialog> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _title;
  late final TextEditingController _message;
  late final TextEditingController _contact;
  int _days = 10;
  String _imageData = '';
  bool _pickingImage = false;

  Future<void> _pickImage() async {
    if (_pickingImage) return;
    setState(() => _pickingImage = true);
    try {
      final picked = await ImagePicker().pickImage(
        source: ImageSource.gallery,
        imageQuality: 80,
        maxWidth: 1600,
        maxHeight: 1600,
      );
      if (picked == null) return;
      final bytes = await picked.readAsBytes();
      final name = picked.name.toLowerCase();
      final mime = name.endsWith('.png') ? 'image/png' : 'image/jpeg';
      final encoded = 'data:$mime;base64,${base64Encode(bytes)}';
      if (encoded.length > 680000) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('حجم الصورة كبير. اختر صورة أقل من 500 كيلوبايت.')),
        );
        return;
      }
      if (mounted) setState(() => _imageData = encoded);
    } finally {
      if (mounted) setState(() => _pickingImage = false);
    }
  }
  @override
  void initState() {
    super.initState();
    _title = TextEditingController(
      text: widget.initial?['title']?.toString() ?? '',
    );
    _message = TextEditingController(
      text: widget.initial?['message']?.toString() ?? '',
    );
    _contact = TextEditingController(
      text: widget.initial?['contact']?.toString() ?? '',
    );
    _imageData = widget.initial?['image_data']?.toString() ?? '';
  }

  @override
  void dispose() {
    _title.dispose();
    _message.dispose();
    _contact.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: AlertDialog(
      title: const Text('طلب إعلان للمراجعة'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Form(
            key: _form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextFormField(
                  controller: _title,
                  maxLength: 120,
                  decoration: const InputDecoration(labelText: 'عنوان الإعلان'),
                  validator: (s) => s == null || s.trim().isEmpty
                      ? 'اكتب عنوان الإعلان'
                      : null,
                ),
                TextFormField(
                  controller: _message,
                  maxLength: 1000,
                  maxLines: 3,
                  decoration: const InputDecoration(labelText: 'نص الإعلان'),
                ),
                TextFormField(
                  controller: _contact,
                  maxLength: 80,
                  decoration: const InputDecoration(labelText: 'وسيلة التواصل'),
                ),
OutlinedButton.icon(
                  onPressed: _pickingImage ? null : _pickImage,
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                  label: Text(_pickingImage
                      ? 'جارٍ تجهيز الصورة…'
                      : _imageData.isEmpty
                          ? 'إضافة صورة الإعلان'
                          : 'تغيير صورة الإعلان'),
                ),
                if (_imageData.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.memory(
                      base64Decode(_imageData.split(',').last),
                      height: 150,
                      width: double.infinity,
                      fit: BoxFit.cover,
                    ),
                  ),
                  TextButton.icon(
                    onPressed: () => setState(() => _imageData = ''),
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('حذف الصورة'),
                  ),
                ],
                DropdownButtonFormField<int>(
                  initialValue: _days,
                  decoration: const InputDecoration(
                    labelText: 'المدة المطلوبة',
                  ),
                  items: [
                    for (var i = 1; i <= 10; i++)
                      DropdownMenuItem(value: i, child: Text('$i يوم')),
                  ],
                  onChanged: (v) => setState(() => _days = v ?? 10),
                ),
                const SizedBox(height: 12),
                const Text(
                  'هذا طلب للمراجعة وليس نشرًا مباشرًا. المدة النهائية تبدأ بعد موافقة الإدارة.',
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
            if (_form.currentState!.validate())
              Navigator.pop(context, {
                'title': _title.text.trim(),
                'message': _message.text.trim(),
                'contact': _contact.text.trim(),
                'requestedDays': _days,
                'imageData': _imageData,
              });
          },
          child: const Text('إرسال للمراجعة'),
        ),
      ],
    ),
  );
}
