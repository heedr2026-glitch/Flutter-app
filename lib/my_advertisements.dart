import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:image_picker/image_picker.dart';

import 'branch_store.dart';
import 'cloud_api.dart';
import 'khdoom_dark_page.dart';

Color _safeAdvertisementColor(String value, Color fallback) {
  if (!RegExp(r'^#[0-9a-fA-F]{6}$').hasMatch(value)) return fallback;
  return Color(int.parse('FF${value.substring(1)}', radix: 16));
}

String _advertisementColorHex(Color color) =>
    '#${color.value.toRadixString(16).substring(2).toUpperCase()}';

const _advertisementColors = <MapEntry<String, Color>>[
  MapEntry('أبيض', Colors.white),
  MapEntry('أسود', Colors.black),
  MapEntry('أزرق', Color(0xFF172554)),
  MapEntry('سماوي', Color(0xFF0369A1)),
  MapEntry('بنفسجي', Color(0xFF653BBC)),
  MapEntry('أخضر', Color(0xFF166534)),
  MapEntry('برتقالي', Color(0xFFC2410C)),
  MapEntry('أحمر', Color(0xFF991B1B)),
  MapEntry('ذهبي', Color(0xFFF59E0B)),
];

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

/// Employees may view approved public advertisements in every package.  A VIP
/// owner keeps the management card on the dashboard instead of a public ad
/// banner, while their staff can still see the public marketplace.
bool showPublicAdvertisementBanner({
  required String subscriptionPackage,
  required bool isEmployee,
}) => isEmployee || subscriptionPackage != 'vip';

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
          displaySeconds: request['displaySeconds'] ?? 8,
          bannerConfig: Map<String, dynamic>.from(
            request['bannerConfig'] ?? const {},
          ),
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
  final String subscriptionPackage;
  const VipAdvertisementCard({super.key, required this.subscriptionPackage});
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
          builder: (_) => MyAdvertisementsPage(
            initialStatus: status,
            canCreate: widget.subscriptionPackage == 'vip',
          ),
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
        MaterialPageRoute(
          builder: (_) => MyAdvertisementsPage(
            canCreate: widget.subscriptionPackage == 'vip',
          ),
        ),
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
  final bool? canCreate;
  const MyAdvertisementsPage({
    super.key,
    this.initialStatus = 'all',
    this.canCreate,
  });
  @override
  State<MyAdvertisementsPage> createState() => _MyAdvertisementsPageState();
}

class AdvertisementBannerView extends StatelessWidget {
  final Map<String, dynamic> ad;
  const AdvertisementBannerView({super.key, required this.ad});

  Map<String, dynamic> get _config {
    final raw = ad['banner_config'];
    if (raw is Map) return Map<String, dynamic>.from(raw);
    if (raw is String && raw.isNotEmpty) {
      try {
        return Map<String, dynamic>.from(jsonDecode(raw) as Map);
      } catch (_) {}
    }
    return const {};
  }

  @override
  Widget build(BuildContext context) {
    final config = _config;
    final textColor = _safeAdvertisementColor(
      config['textColor']?.toString() ?? '#FFFFFF',
      Colors.white,
    );
    final barColor = _safeAdvertisementColor(
      config['barColor']?.toString() ?? '#172554',
      const Color(0xFF172554),
    );
    final fontSize =
        (double.tryParse(config['fontSize']?.toString() ?? '') ?? 19)
            .clamp(12, 32)
            .toDouble();
    final scale = (double.tryParse(config['logoScale']?.toString() ?? '') ?? 1)
        .clamp(.5, 1.5)
        .toDouble();
    final imageData =
        ad['image_data']?.toString() ?? ad['imageData']?.toString() ?? '';
    final fullBannerImage =
        config['adType']?.toString() == 'image' &&
        (config['bannerImageData']?.toString().isNotEmpty ?? false);
    if (fullBannerImage) {
      final bannerImageData = config['bannerImageData']!.toString();
      return Container(
        width: double.infinity,
        decoration: BoxDecoration(
          color: barColor,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: const Color(0xFFF59E0B), width: 1.5),
        ),
        child: AspectRatio(
          aspectRatio: 4,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(14),
            child: Image.memory(
              base64Decode(bannerImageData.split(',').last),
              width: double.infinity,
              height: double.infinity,
              fit: BoxFit.cover,
              errorBuilder: (_, _, _) => Center(
                child: Icon(Icons.broken_image_outlined, color: textColor),
              ),
            ),
          ),
        ),
      );
    }
    final logoPosition = config['logoPosition']?.toString() ?? 'left';
    final textAlignValue = config['textAlign']?.toString() ?? 'right';
    final textAlign = textAlignValue == 'left'
        ? TextAlign.left
        : textAlignValue == 'center'
        ? TextAlign.center
        : TextAlign.right;
    final logo = imageData.isEmpty
        ? const Icon(Icons.campaign, color: Color(0xFFF59E0B), size: 46)
        : ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Image.memory(
              base64Decode(imageData.split(',').last),
              width: (72 * scale).clamp(40, 108).toDouble(),
              height: (72 * scale).clamp(40, 108).toDouble(),
              fit: BoxFit.contain,
              errorBuilder: (_, _, _) => const Icon(
                Icons.campaign,
                color: Color(0xFFF59E0B),
                size: 46,
              ),
            ),
          );
    final textBlock = Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: textAlign == TextAlign.left
          ? CrossAxisAlignment.start
          : textAlign == TextAlign.center
          ? CrossAxisAlignment.center
          : CrossAxisAlignment.end,
      children: [
        Text(
          ad['title']?.toString().trim().isNotEmpty == true
              ? ad['title'].toString()
              : 'إعلان',
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          textAlign: textAlign,
          style: TextStyle(
            color: textColor,
            fontSize: fontSize,
            fontWeight: FontWeight.bold,
          ),
        ),
        const SizedBox(height: 3),
        Text(
          ad['message']?.toString().trim().isNotEmpty == true
              ? ad['message'].toString()
              : 'نص الإعلان سيظهر هنا',
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
          textAlign: textAlign,
          style: TextStyle(
            color: textColor.withValues(alpha: .82),
            fontSize: (fontSize - 3).clamp(12, 28),
            height: 1.4,
          ),
        ),
        const SizedBox(height: 7),
        const Text(
          'عرض تفاصيل الإعلان',
          style: TextStyle(
            color: Color(0xFFFBBF24),
            fontSize: 13,
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    );
    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: barColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFFF59E0B), width: 1.5),
      ),
      child: AspectRatio(
        aspectRatio: 4,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: Stack(
            children: [
              Positioned.fill(
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 8,
                  ),
                  child: Align(
                    alignment: textAlignValue == 'left'
                        ? Alignment.centerLeft
                        : textAlignValue == 'center'
                        ? Alignment.center
                        : Alignment.centerRight,
                    child: FractionallySizedBox(
                      widthFactor: .72,
                      child: textBlock,
                    ),
                  ),
                ),
              ),
              if (imageData.isNotEmpty)
                Positioned.fill(
                  child: Align(
                    alignment: logoPosition == 'right'
                        ? Alignment.centerRight
                        : logoPosition == 'center'
                        ? Alignment.center
                        : Alignment.centerLeft,
                    child: FractionallySizedBox(
                      widthFactor: .23,
                      child: Center(child: logo),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MyAdvertisementsPageState extends State<MyAdvertisementsPage>
    with WidgetsBindingObserver {
  List<Map<String, dynamic>> _ads = [];
  late String _selectedStatus;
  String? _error;
  bool _loading = true;
  bool _submitting = false;
  bool _canCreate = false;
  bool _accessChecked = false;
  bool _fetching = false;
  DateTime? _updatedAt;
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    _selectedStatus = advertisementGroups.containsKey(widget.initialStatus)
        ? widget.initialStatus
        : 'all';
    _canCreate = widget.canCreate ?? false;
    WidgetsBinding.instance.addObserver(this);
    // The cached package can be stale after an admin changes an institution's
    // subscription. Always refresh the server-side package before deciding
    // whether this institution may submit an advertisement.
    _loadPackageAccess();
    _load();
    _timer = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  Future<void> _loadPackageAccess() async {
    final prefs = await BranchPreferences.getInstance();
    try {
      final subscription = await MyAdsService.withApi(
        (api) => api.subscription(),
      );
      final package = subscription['package']?.toString().toLowerCase() ?? '';
      if (package.isNotEmpty) {
        await prefs.setString('subscription_package', package);
      }
      if (!mounted) return;
      setState(() {
        _canCreate = package == 'vip';
        _accessChecked = true;
      });
    } catch (_) {
      // Keep the supplied/cached value only when the server is temporarily
      // unavailable; the POST endpoint still enforces the real permission.
      if (!mounted) return;
      setState(() {
        _canCreate =
            (prefs.getString('subscription_package') ??
                (widget.canCreate == true ? 'vip' : 'free')) ==
            'vip';
        _accessChecked = true;
      });
    }
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
    previous ??= await _loadDraft();
    final request = await showDialog<Map<String, dynamic>>(
      context: context,
      useSafeArea: false,
      barrierDismissible: false,
      builder: (_) => KhdoomDarkPage(
        child: AdvertisementRequestDialog(initial: previous, fullScreen: true),
      ),
    );
    if (!mounted || request == null) return;
    final action = request['_action']?.toString() ?? 'submit';
    if (action == 'save') {
      final draft = Map<String, dynamic>.from(request)..remove('_action');
      final prefs = await BranchPreferences.getInstance();
      await prefs.setString('advertisement_draft_v1', jsonEncode(draft));
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('تم حفظ مسودة الإعلان')));
      }
      return;
    }
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

  Future<Map<String, dynamic>?> _loadDraft() async {
    final prefs = await BranchPreferences.getInstance();
    final raw = prefs.getString('advertisement_draft_v1');
    if (raw == null || raw.isEmpty) return null;
    try {
      return Map<String, dynamic>.from(jsonDecode(raw) as Map);
    } catch (_) {
      return null;
    }
  }

  Future<void> _deleteRejected(Map<String, dynamic> ad) async {
    final id = int.tryParse(ad['id']?.toString() ?? '');
    if (id == null || advertisementStatus(ad) != 'rejected') return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('حذف الإعلان المرفوض؟'),
        content: const Text(
          'سيختفي من قائمة إعلاناتك نهائيًا، ولن يؤثر على الإعلانات المنشورة.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('إلغاء'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('حذف'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _submitting = true);
    try {
      await MyAdsService.withApi((api) => api.deleteAdvertisement(id));
      if (mounted) {
        setState(
          () => _ads.removeWhere(
            (item) => item['id'].toString() == id.toString(),
          ),
        );
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('تم حذف الإعلان المرفوض')));
      }
    } catch (error) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              error is CloudApiException ? error.message : 'تعذر حذف الإعلان',
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
  Widget build(BuildContext context) {
    if (!_accessChecked) {
      return const KhdoomDarkPage(
        child: Center(child: CircularProgressIndicator()),
      );
    }
    if (!_canCreate) {
      return KhdoomDarkPage(
        child: Center(
          child: Card(
            margin: const EdgeInsets.all(24),
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(
                    Icons.lock_outline,
                    size: 48,
                    color: Color(0xFFF59E0B),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'إنشاء الإعلان متاح في باقة VIP فقط',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'يمكنك مشاهدة الإعلانات المنشورة، لكن لا يمكن إنشاء إعلان جديد من الباقة الحالية.',
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }
    return KhdoomDarkPage(
      child: AdvertisementRequestDialog(
        fullScreen: true,
        onCompleted: _handleRequestResult,
      ),
    );
  }

  Future<void> _handleRequestResult(Map<String, dynamic> request) async {
    if (!mounted) return;
    final action = request['_action']?.toString() ?? 'submit';
    if (action == 'save') {
      final draft = Map<String, dynamic>.from(request)..remove('_action');
      final prefs = await BranchPreferences.getInstance();
      await prefs.setString('advertisement_draft_v1', jsonEncode(draft));
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('تم حفظ مسودة الإعلان')));
      }
      return;
    }
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
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              error is CloudApiException
                  ? error.message
                  : 'لم نتأكد من الإرسال. حاول مرة أخرى.',
            ),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }
}

/*
  Widget _legacyBuild(BuildContext context) => KhdoomDarkPage(
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('الإعلانات'),
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
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      FilledButton.icon(
                        onPressed: _submitting ? null : () => _request(),
                        icon: const Icon(Icons.add),
                        label: Text(
                          _submitting ? 'جارٍ الإرسال…' : 'إنشاء إعلان',
                        ),
                      ),
                      OutlinedButton.icon(
                        onPressed: () =>
                            setState(() => _selectedStatus = 'published'),
                        icon: const Icon(Icons.campaign_outlined),
                        label: const Text('المنشور'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'تختار مدة الطلب من 1 إلى 10 أيام. الإدارة تراجع الإعلان وتحدد المدة النهائية، وقد تزيدها أو تقللها.',
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'حالة الإعلان',
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                  ),
                  const SizedBox(height: 6),
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
                            if ([
                              'expired',
                              'rejected',
                              'paused',
                            ].contains(advertisementStatus(ad)))
                              Wrap(
                                spacing: 8,
                                children: [
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
                                  if (advertisementStatus(ad) == 'rejected')
                                    TextButton.icon(
                                      onPressed: _submitting
                                          ? null
                                          : () => _deleteRejected(ad),
                                      icon: const Icon(Icons.delete_outline),
                                      label: const Text('حذف المرفوض'),
                                      style: TextButton.styleFrom(
                                        foregroundColor: Colors.redAccent,
                                      ),
                                    ),
                                ],
                              ),
                            const SizedBox(height: 12),
                            const Text(
                              'المعاينة كما ستظهر في الصفحة الرئيسية',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            const SizedBox(height: 8),
                            AdvertisementBannerView(ad: ad),
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
  */

// The legacy list page is intentionally no longer used. The editor is now
// the entry screen for the institution's advertisements.

class AdvertisementRequestDialog extends StatefulWidget {
  final Map<String, dynamic>? initial;
  final bool fullScreen;
  final ValueChanged<Map<String, dynamic>>? onCompleted;
  const AdvertisementRequestDialog({
    super.key,
    this.initial,
    this.fullScreen = false,
    this.onCompleted,
  });
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
  String _textColor = '#FFFFFF';
  String _barColor = '#172554';
  String _textAlign = 'right';
  String _logoPosition = 'right';
  double _logoScale = 1;
  double _imageWidth = 120;
  double _imageHeight = 90;
  double _height = 145;
  double _fontSize = 18;
  String _fontFamily = 'default';
  int _displaySeconds = 5;
  double _textX = .5;
  double _textY = .5;
  double _messageX = .5;
  double _messageY = .75;
  double _logoX = .88;
  double _logoY = .5;
  double _bannerX = .5;
  double _bannerY = .5;
  double _bannerWidth = .96;
  double _bannerHeight = .96;
  String _selectedElement = 'text';
  String _imageData = '';
  String _bannerImageData = '';
  bool _fullImageMode = false;
  bool _showAdditionalText = false;
  bool _pickingImage = false;

  int get _logoSize => (72 * _logoScale).round();

  Map<String, dynamic> _payload(String action) => {
    '_action': action,
    // The backend requires a title for every ad record.  A full-banner image
    // can be submitted without visible text, so give it a stable internal
    // title instead of failing validation at publish time.
    'title': _title.text.trim().isEmpty && _fullImageMode
        ? 'إعلان صورة'
        : _title.text.trim(),
    'message': _message.text.trim(),
    'contact': _contact.text.trim(),
    'requestedDays': _days,
    'displaySeconds': _displaySeconds,
    'bannerConfig': {
      'textColor': _textColor,
      'barColor': _barColor,
      'textAlign': _textAlign,
      'logoPosition': _logoPosition,
      'logoScale': _logoScale,
      'imageWidth': _imageWidth,
      'imageHeight': _imageHeight,
      'height': _height,
      'fontSize': _fontSize,
      'fontFamily': _fontFamily,
      'textX': _textX,
      'textY': _textY,
      'messageX': _messageX,
      'messageY': _messageY,
      'logoX': _logoX,
      'logoY': _logoY,
      'bannerX': _bannerX,
      'bannerY': _bannerY,
      'bannerWidth': _bannerWidth,
      'bannerHeight': _bannerHeight,
      'adType': _fullImageMode ? 'image' : 'text',
      'bannerImageData': _bannerImageData,
    },
    'imageData': _imageData,
  };

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
        if (mounted)
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('حجم الصورة كبير. اختر صورة أقل من 500 كيلوبايت.'),
            ),
          );
        return;
      }
      if (mounted) setState(() => _imageData = encoded);
    } finally {
      if (mounted) setState(() => _pickingImage = false);
    }
  }

  Future<void> _pickFullBannerImage() async {
    if (_pickingImage) return;
    setState(() => _pickingImage = true);
    try {
      final picked = await ImagePicker().pickImage(
        source: ImageSource.gallery,
        imageQuality: 88,
        maxWidth: 1800,
        maxHeight: 600,
      );
      if (picked == null) return;
      final bytes = await picked.readAsBytes();
      final name = picked.name.toLowerCase();
      final mime = name.endsWith('.png') ? 'image/png' : 'image/jpeg';
      final encoded = 'data:$mime;base64,${base64Encode(bytes)}';
      if (encoded.length > 900000) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(
              content: Text('حجم صورة الإعلان كبير. اختر صورة أخف.'),
            ),
          );
        }
        return;
      }
      if (mounted) {
        setState(() {
          _bannerImageData = encoded;
          _fullImageMode = true;
          _bannerX = .5;
          _bannerY = .5;
          _bannerWidth = 1;
          _bannerHeight = 1;
        });
      }
    } finally {
      if (mounted) setState(() => _pickingImage = false);
    }
  }

  void _deleteSelectedElement() {
    setState(() {
      switch (_selectedElement) {
        case 'title':
          _title.clear();
          break;
        case 'message':
          _message.clear();
          _showAdditionalText = false;
          break;
        case 'banner':
          _bannerImageData = '';
          _fullImageMode = false;
          _bannerX = .5;
          _bannerY = .5;
          _bannerWidth = .96;
          _bannerHeight = .96;
          break;
        case 'logo':
        default:
          _imageData = '';
          break;
      }
      _selectedElement = 'title';
    });
  }

  Widget _colorChooser({
    required String title,
    required String value,
    required ValueChanged<String> onChanged,
  }) {
    final selected = _safeAdvertisementColor(value, Colors.white);
    return InkWell(
      borderRadius: BorderRadius.circular(10),
      onTap: () async {
        final chosen = await showModalBottomSheet<String>(
          context: context,
          backgroundColor: const Color(0xFF08234A),
          builder: (sheetContext) => SafeArea(
            child: Directionality(
              textDirection: TextDirection.rtl,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Wrap(
                  spacing: 14,
                  runSpacing: 14,
                  children: [
                    for (final option in _advertisementColors)
                      InkWell(
                        onTap: () => Navigator.pop(
                          sheetContext,
                          _advertisementColorHex(option.value),
                        ),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            CircleAvatar(
                              radius: 22,
                              backgroundColor: option.value,
                              child: option.value.value == selected.value
                                  ? Icon(
                                      Icons.check,
                                      color:
                                          option.value.computeLuminance() > .5
                                          ? Colors.black
                                          : Colors.white,
                                    )
                                  : null,
                            ),
                            const SizedBox(height: 3),
                            Text(
                              option.key,
                              style: const TextStyle(fontSize: 11),
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        );
        if (chosen != null && mounted) setState(() => onChanged(chosen));
      },
      child: Row(
        children: [
          Expanded(child: Text(title)),
          CircleAvatar(radius: 16, backgroundColor: selected),
          const SizedBox(width: 6),
          const Icon(Icons.arrow_drop_down),
        ],
      ),
    );
  }

  double _bounded(double value, double min, double max) =>
      value.clamp(min, max).toDouble();

  Widget _buildBannerPreview({bool showTools = true}) {
    final text = _message.text.trim();
    final logoWidth = (_imageWidth * _logoScale).clamp(40, 600).toDouble();
    final logoHeight = (_imageHeight * _logoScale).clamp(30, 300).toDouble();
    final logo = _imageData.isEmpty
        ? const SizedBox.shrink()
        : ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.memory(
              base64Decode(_imageData.split(',').last),
              width: logoWidth,
              height: logoHeight,
              fit: BoxFit.cover,
              errorBuilder: (_, _, _) => const Icon(
                Icons.broken_image_outlined,
                color: Colors.white70,
              ),
            ),
          );
    final previewTextStyle = TextStyle(
      fontFamily: _fontFamily == 'default' ? null : _fontFamily,
    );
    final alignment = _textAlign == 'left'
        ? TextAlign.left
        : _textAlign == 'center'
        ? TextAlign.center
        : TextAlign.right;

    Widget textElement({required bool titleElement}) {
      final value = titleElement ? _title.text.trim() : text;
      final selected = titleElement
          ? _selectedElement == 'title'
          : _selectedElement == 'message';
      return GestureDetector(
        onTap: showTools
            ? () => setState(
                () => _selectedElement = titleElement ? 'title' : 'message',
              )
            : null,
        onPanUpdate: showTools
            ? (details) {
                setState(() {
                  _selectedElement = titleElement ? 'title' : 'message';
                  if (titleElement) {
                    _textX = _bounded(
                      _textX + details.delta.dx / 300,
                      .08,
                      .92,
                    );
                    _textY = _bounded(
                      _textY + details.delta.dy / _height,
                      .12,
                      .88,
                    );
                  } else {
                    _messageX = _bounded(
                      _messageX + details.delta.dx / 300,
                      .08,
                      .92,
                    );
                    _messageY = _bounded(
                      _messageY + details.delta.dy / _height,
                      .12,
                      .88,
                    );
                  }
                });
              }
            : null,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
          decoration: showTools && selected
              ? BoxDecoration(
                  border: Border.all(color: Colors.white70),
                  borderRadius: BorderRadius.circular(6),
                )
              : null,
          child: Text(
            value,
            maxLines: titleElement ? 2 : 3,
            overflow: TextOverflow.ellipsis,
            textAlign: alignment,
            textDirection: TextDirection.rtl,
            style: TextStyle(
              fontFamily: previewTextStyle.fontFamily,
              color: _safeAdvertisementColor(
                _textColor,
                Colors.white,
              ).withValues(alpha: titleElement ? 1 : .82),
              fontSize: titleElement
                  ? _fontSize
                  : (_fontSize - 3).clamp(12, 28),
              fontWeight: titleElement ? FontWeight.bold : FontWeight.normal,
              height: 1.25,
            ),
          ),
        ),
      );
    }

    final titleWidget = textElement(titleElement: true);
    final messageWidget = text.isEmpty
        ? null
        : textElement(titleElement: false);
    final logoWidget = GestureDetector(
      onTap: showTools ? () => setState(() => _selectedElement = 'logo') : null,
      onScaleUpdate: showTools
          ? (details) {
              setState(() {
                _selectedElement = 'logo';
                _logoScale = _bounded(_logoScale * details.scale, .5, 1.5);
                _logoX = _bounded(
                  _logoX + details.focalPointDelta.dx / 300,
                  .08,
                  .92,
                );
                _logoY = _bounded(
                  _logoY + details.focalPointDelta.dy / _height,
                  .15,
                  .85,
                );
              });
            }
          : null,
      child: Container(
        padding: const EdgeInsets.all(3),
        decoration: showTools && _selectedElement == 'logo'
            ? BoxDecoration(
                border: Border.all(color: Colors.white70),
                borderRadius: BorderRadius.circular(8),
              )
            : null,
        child: logo,
      ),
    );
    final fullBannerImage = _fullImageMode && _bannerImageData.isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        LayoutBuilder(
          builder: (context, outer) => AspectRatio(
            aspectRatio: 4,
            child: Container(
              width: double.infinity,
              clipBehavior: Clip.hardEdge,
              decoration: BoxDecoration(
                color: _safeAdvertisementColor(
                  _barColor,
                  const Color(0xFF172554),
                ),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFFF59E0B), width: 1.5),
              ),
              child: Padding(
                // الصورة الكاملة هي الإعلان نفسه؛ لا تضف لها هامشًا داخليًا.
                // وضع الشعار والنص يحتفظ بالمسافة الآمنة حول العناصر.
                padding: fullBannerImage
                    ? EdgeInsets.zero
                    : EdgeInsets.all((outer.maxWidth * .025).clamp(8, 18)),
                child: LayoutBuilder(
                  builder: (context, constraints) => fullBannerImage
                      ? Stack(
                          clipBehavior: Clip.hardEdge,
                          children: [
                            Positioned.fill(
                              child: GestureDetector(
                                onTap: showTools
                                    ? () => setState(
                                        () => _selectedElement = 'banner',
                                      )
                                    : null,
                                onScaleUpdate: showTools
                                    ? (details) {
                                        setState(() {
                                          _selectedElement = 'banner';
                                          // الصورة الكاملة هي خلفية الشريط؛
                                          // لا نسمح بتصغيرها أو إزاحتها بحيث
                                          // يظهر لون الشريط حولها.
                                          _bannerX = .5;
                                          _bannerY = .5;
                                          if (details.scale != 1) {
                                            _bannerWidth = _bounded(
                                              _bannerWidth * details.scale,
                                              1,
                                              1.5,
                                            );
                                            _bannerHeight = _bounded(
                                              _bannerHeight * details.scale,
                                              1,
                                              1.5,
                                            );
                                          }
                                        });
                                      }
                                    : null,
                                child: Container(
                                  decoration:
                                      showTools && _selectedElement == 'banner'
                                      ? BoxDecoration(
                                          border: Border.all(
                                            color: Colors.white70,
                                          ),
                                        )
                                      : null,
                                  child: Image.memory(
                                    base64Decode(
                                      _bannerImageData.split(',').last,
                                    ),
                                    // يحافظ على نسبة الصورة ويملأ مساحة الشريط
                                    // مع قص الحواف الزائدة بدل تشويهها.
                                    fit: BoxFit.cover,
                                    errorBuilder: (_, _, _) =>
                                        const SizedBox.shrink(),
                                  ),
                                ),
                              ),
                            ),
                          ],
                        )
                      : Stack(
                          clipBehavior: Clip.hardEdge,
                          children: [
                            if (_title.text.trim().isNotEmpty)
                              Positioned(
                                left:
                                    constraints.maxWidth * _textX -
                                    constraints.maxWidth * .36,
                                top: constraints.maxHeight * _textY - 24,
                                width: constraints.maxWidth * .72,
                                height: constraints.maxHeight * .42,
                                child: FittedBox(
                                  fit: BoxFit.scaleDown,
                                  alignment: Alignment.center,
                                  child: titleWidget,
                                ),
                              ),
                            if (messageWidget != null)
                              Positioned(
                                left:
                                    constraints.maxWidth * _messageX -
                                    constraints.maxWidth * .36,
                                top: constraints.maxHeight * _messageY - 18,
                                width: constraints.maxWidth * .72,
                                height: constraints.maxHeight * .38,
                                child: FittedBox(
                                  fit: BoxFit.scaleDown,
                                  alignment: Alignment.center,
                                  child: messageWidget,
                                ),
                              ),
                            Positioned(
                              left:
                                  constraints.maxWidth * _logoX - logoWidth / 2,
                              top:
                                  constraints.maxHeight * _logoY -
                                  logoHeight / 2,
                              child: logoWidget,
                            ),
                          ],
                        ),
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        if (showTools)
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              ChoiceChip(
                label: const Text('T كتابة'),
                selected: _selectedElement == 'text',
                onSelected: (_) => setState(() => _selectedElement = 'text'),
              ),
              ChoiceChip(
                label: const Text('الشعار'),
                selected: _selectedElement == 'logo',
                onSelected: (_) => setState(() => _selectedElement = 'logo'),
              ),
              OutlinedButton.icon(
                onPressed: _pickingImage ? null : _pickImage,
                icon: const Icon(Icons.add_photo_alternate_outlined),
                label: const Text('إضافة صورة'),
              ),
              OutlinedButton.icon(
                onPressed: _deleteSelectedElement,
                icon: const Icon(Icons.delete_outline),
                label: const Text('حذف المحدد'),
              ),
            ],
          ),
        const SizedBox(height: 4),
        Text(
          'هذه هي الهيئة التي ستظهر للمستخدم بعد الموافقة.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 12),
      ],
    );
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
    final rawConfig = widget.initial?['banner_config'];
    Map<String, dynamic> config = {};
    try {
      if (rawConfig is Map) config = Map<String, dynamic>.from(rawConfig);
      if (rawConfig is String && rawConfig.isNotEmpty) {
        config = Map<String, dynamic>.from(jsonDecode(rawConfig) as Map);
      }
    } catch (_) {}
    _textColor = config['textColor']?.toString() ?? _textColor;
    _barColor = config['barColor']?.toString() ?? _barColor;
    _textAlign = config['textAlign']?.toString() ?? _textAlign;
    _logoPosition = config['logoPosition']?.toString() ?? _logoPosition;
    _logoScale =
        double.tryParse(config['logoScale']?.toString() ?? '')
            ?.clamp(.5, 1.5) ??
        1;
    _height =
        double.tryParse(config['height']?.toString() ?? '')?.clamp(80, 180) ??
        145;
    _fontSize =
        double.tryParse(config['fontSize']?.toString() ?? '')?.clamp(12, 32) ??
        28;
    _fontFamily = config['fontFamily']?.toString() ?? 'default';
    _displaySeconds =
        int.tryParse(widget.initial?['display_seconds']?.toString() ?? '') ??
        int.tryParse(widget.initial?['displaySeconds']?.toString() ?? '') ??
        5;
    _textX =
        double.tryParse(config['textX']?.toString() ?? '')?.clamp(.08, .92) ??
        .5;
    _textY =
        double.tryParse(config['textY']?.toString() ?? '')?.clamp(.15, .85) ??
        .5;
    _messageX =
        double.tryParse(config['messageX']?.toString() ?? '')
            ?.clamp(.08, .92) ??
        .5;
    _messageY =
        double.tryParse(config['messageY']?.toString() ?? '')
            ?.clamp(.12, .88) ??
        .75;
    _logoX =
        double.tryParse(config['logoX']?.toString() ?? '')?.clamp(.08, .92) ??
        (_logoPosition == 'left' ? .12 : .88);
    _logoY =
        double.tryParse(config['logoY']?.toString() ?? '')?.clamp(.15, .85) ??
        .5;
    _bannerX =
        double.tryParse(config['bannerX']?.toString() ?? '')?.clamp(.08, .92) ??
        .5;
    _bannerY =
        double.tryParse(config['bannerY']?.toString() ?? '')?.clamp(.08, .92) ??
        .5;
    _bannerWidth =
        double.tryParse(config['bannerWidth']?.toString() ?? '')
            ?.clamp(.2, 1.5) ??
        .96;
    _bannerHeight =
        double.tryParse(config['bannerHeight']?.toString() ?? '')
            ?.clamp(.2, 1.5) ??
        .96;
    _imageData = widget.initial?['image_data']?.toString() ?? '';
    _bannerImageData =
        config['bannerImageData']?.toString() ??
        widget.initial?['banner_image_data']?.toString() ??
        '';
    _showAdditionalText = _message.text.trim().isNotEmpty;
    _fullImageMode =
        config['adType']?.toString() == 'image' && _bannerImageData.isNotEmpty;
    if (_fullImageMode) {
      // A full-banner image is the bar itself, not a smaller object inside it.
      _bannerX = .5;
      _bannerY = .5;
      _bannerWidth = 1;
      _bannerHeight = 1;
    }
    if (widget.initial == null && widget.fullScreen) {
      _textAlign = 'center';
      _logoPosition = 'left';
      _logoX = .12;
      _fontSize = 28;
      _barColor = '#0369A1';
    }
  }

  @override
  void dispose() {
    _title.dispose();
    _message.dispose();
    _contact.dispose();
    super.dispose();
  }

  Widget _settingCard({required Widget child}) => Container(
    width: double.infinity,
    margin: const EdgeInsets.only(bottom: 10),
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: const Color(0xFF08234A).withValues(alpha: .72),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: const Color(0xFF2B5A9B)),
    ),
    child: child,
  );

  Future<void> _chooseFont() async {
    final value = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF08234A),
      builder: (sheetContext) => SafeArea(
        child: Directionality(
          textDirection: TextDirection.rtl,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'اختيار الخط',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 8),
                for (final item in const [
                  ('default', 'الافتراضي'),
                  ('sans-serif', 'واضح'),
                  ('serif', 'كلاسيكي'),
                ])
                  ListTile(
                    title: Text(
                      item.$2,
                      style: TextStyle(
                        fontFamily: item.$1 == 'default' ? null : item.$1,
                      ),
                    ),
                    trailing: _fontFamily == item.$1
                        ? const Icon(Icons.check, color: Color(0xFF38BDF8))
                        : null,
                    onTap: () => Navigator.pop(sheetContext, item.$1),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
    if (value != null && mounted) setState(() => _fontFamily = value);
  }

  Widget _stepper({
    required String label,
    required IconData icon,
    required String value,
    required VoidCallback onMinus,
    required VoidCallback onPlus,
  }) => _settingCard(
    child: Row(
      children: [
        Icon(icon, color: const Color(0xFF38BDF8)),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            label,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        ),
        IconButton(
          onPressed: onMinus,
          padding: EdgeInsets.zero,
          visualDensity: VisualDensity.compact,
          constraints: const BoxConstraints.tightFor(width: 32, height: 32),
          icon: const Icon(Icons.remove_circle_outline),
        ),
        SizedBox(
          width: 42,
          child: Center(
            child: Text(
              value,
              style: const TextStyle(fontSize: 17, fontWeight: FontWeight.bold),
            ),
          ),
        ),
        IconButton(
          onPressed: onPlus,
          padding: EdgeInsets.zero,
          visualDensity: VisualDensity.compact,
          constraints: const BoxConstraints.tightFor(width: 32, height: 32),
          icon: const Icon(Icons.add_circle_outline),
        ),
      ],
    ),
  );

  Widget _positionSelector({
    required String label,
    required String selected,
    required ValueChanged<String> onChanged,
  }) => _settingCard(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Row(
          children: [
            for (final item in const [
              ('right', 'يمين'),
              ('center', 'وسط'),
              ('left', 'يسار'),
            ])
              Expanded(
                child: Padding(
                  padding: const EdgeInsetsDirectional.only(end: 5),
                  child: ChoiceChip(
                    label: SizedBox(
                      width: double.infinity,
                      child: Text(item.$2, textAlign: TextAlign.center),
                    ),
                    selected: selected == item.$1,
                    onSelected: (_) => onChanged(item.$1),
                  ),
                ),
              ),
          ],
        ),
      ],
    ),
  );

  Widget _referenceCard({required Widget child}) => Container(
    width: double.infinity,
    margin: const EdgeInsets.only(bottom: 10),
    padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
    decoration: BoxDecoration(
      color: const Color(0xFF0A2349),
      borderRadius: BorderRadius.circular(14),
      border: Border.all(color: const Color(0xFF204A80)),
    ),
    child: child,
  );

  Widget _referencePosition({
    required String label,
    required String selected,
    required ValueChanged<String> onChanged,
  }) => _referenceCard(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 13)),
        const SizedBox(height: 7),
        Row(
          children: [
            for (final item in const [
              ('right', Icons.format_align_right),
              ('center', Icons.format_align_center),
              ('left', Icons.format_align_left),
            ])
              Expanded(
                child: Padding(
                  padding: EdgeInsets.zero,
                  child: ChoiceChip(
                    visualDensity: VisualDensity.compact,
                    label: Icon(item.$2, size: 18),
                    selected: selected == item.$1,
                    onSelected: (_) => onChanged(item.$1),
                  ),
                ),
              ),
          ],
        ),
      ],
    ),
  );

  Widget _referenceStepper({
    required String label,
    required IconData icon,
    required String value,
    required VoidCallback onMinus,
    required VoidCallback onPlus,
  }) => LayoutBuilder(
    builder: (context, constraints) {
      final compact = constraints.maxWidth < 150;
      return Row(
        children: [
          if (!compact) ...[
            Icon(icon, color: const Color(0xFF38BDF8), size: 20),
            const SizedBox(width: 4),
          ],
          Expanded(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontWeight: FontWeight.w600,
                fontSize: compact ? 11 : 14,
              ),
            ),
          ),
          IconButton(
            onPressed: onMinus,
            padding: EdgeInsets.zero,
            visualDensity: VisualDensity.compact,
            constraints: BoxConstraints.tightFor(
              width: compact ? 26 : 32,
              height: compact ? 28 : 32,
            ),
            icon: Icon(Icons.remove_circle_outline, size: compact ? 20 : 22),
          ),
          SizedBox(
            width: compact ? 28 : 38,
            child: Center(
              child: Text(
                value,
                style: TextStyle(
                  fontSize: compact ? 14 : 16,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
          IconButton(
            onPressed: onPlus,
            padding: EdgeInsets.zero,
            visualDensity: VisualDensity.compact,
            constraints: BoxConstraints.tightFor(
              width: compact ? 26 : 32,
              height: compact ? 28 : 32,
            ),
            icon: Icon(Icons.add_circle_outline, size: compact ? 20 : 22),
          ),
        ],
      );
    },
  );

  Widget _newMobileEditorPage(BuildContext context) {
    final editor = Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.add_circle_outline),
                  label: const Text('إنشاء إعلان'),
                  style: FilledButton.styleFrom(
                    minimumSize: const Size(0, 36),
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    textStyle: const TextStyle(fontSize: 12),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.receipt_long_outlined),
                  label: const Text('الطلبات'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 36),
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    textStyle: const TextStyle(fontSize: 12),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.campaign_outlined),
                  label: const Text('المنشور'),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, 36),
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    textStyle: const TextStyle(fontSize: 12),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _referenceCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: _fullImageMode
                          ? OutlinedButton.icon(
                              onPressed: () =>
                                  setState(() => _fullImageMode = false),
                              icon: const Icon(Icons.text_fields),
                              label: const Text('شعار + نص'),
                            )
                          : FilledButton.icon(
                              onPressed: () {},
                              icon: const Icon(Icons.text_fields),
                              label: const Text('شعار + نص'),
                            ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _fullImageMode
                          ? FilledButton.icon(
                              onPressed: () {},
                              icon: const Icon(Icons.image_outlined),
                              label: const Text('صورة كاملة'),
                            )
                          : OutlinedButton.icon(
                              onPressed: () =>
                                  setState(() => _fullImageMode = true),
                              icon: const Icon(Icons.image_outlined),
                              label: const Text('صورة كاملة'),
                            ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                TextFormField(
                  controller: _title,
                  maxLength: 100,
                  decoration: const InputDecoration(
                    labelText: 'النص الرئيسي',
                    hintText: 'اكتب النص الرئيسي هنا',
                    prefixIcon: Icon(Icons.text_fields),
                  ),
                  onChanged: (_) => setState(() {}),
                  validator: (s) => s == null || s.trim().isEmpty
                      ? (_fullImageMode && _bannerImageData.isNotEmpty
                            ? null
                            : 'اكتب النص الرئيسي')
                      : null,
                ),
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton.icon(
                    onPressed: () => setState(
                      () => _showAdditionalText = !_showAdditionalText,
                    ),
                    icon: Icon(
                      _showAdditionalText
                          ? Icons.remove_circle_outline
                          : Icons.add_circle_outline,
                    ),
                    label: Text(
                      _showAdditionalText ? 'إخفاء النص الإضافي' : 'إضافة نص',
                    ),
                  ),
                ),
                if (_showAdditionalText)
                  TextFormField(
                    controller: _message,
                    maxLength: 120,
                    maxLines: 1,
                    decoration: const InputDecoration(
                      labelText: 'نص إضافي اختياري',
                      hintText: 'اكتب نصًا ثانيًا للإعلان',
                      prefixIcon: Icon(Icons.subtitles_outlined),
                    ),
                    onChanged: (_) => setState(() {}),
                  ),
                const SizedBox(height: 8),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final textColor = _colorChooser(
                      title: 'لون الخط',
                      value: _textColor,
                      onChanged: (v) => setState(() => _textColor = v),
                    );
                    final barColor = _colorChooser(
                      title: 'لون الشريط',
                      value: _barColor,
                      onChanged: (v) => setState(() => _barColor = v),
                    );
                    return constraints.maxWidth >= 320
                        ? Row(
                            children: [
                              Expanded(child: textColor),
                              const SizedBox(width: 8),
                              Expanded(child: barColor),
                            ],
                          )
                        : Column(
                            children: [
                              textColor,
                              const SizedBox(height: 8),
                              barColor,
                            ],
                          );
                  },
                ),
                const SizedBox(height: 8),
                _referenceStepper(
                  label: 'حجم الخط',
                  icon: Icons.format_size,
                  value: '${_fontSize.round()}',
                  onMinus: () => setState(
                    () => _fontSize = _bounded(_fontSize - 1, 12, 48),
                  ),
                  onPlus: () => setState(
                    () => _fontSize = _bounded(_fontSize + 1, 12, 48),
                  ),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: _pickingImage
                      ? null
                      : (_fullImageMode ? _pickFullBannerImage : _pickImage),
                  icon: const Icon(Icons.add_photo_alternate_outlined),
                  label: Text(
                    _fullImageMode
                        ? 'إضافة صورة الإعلان الكاملة'
                        : 'إضافة صورة أو شعار',
                  ),
                ),
                if (_fullImageMode && _bannerImageData.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  OutlinedButton.icon(
                    onPressed: () => setState(() {
                      _bannerX = .5;
                      _bannerY = .5;
                      _bannerWidth = 1;
                      _bannerHeight = 1;
                    }),
                    icon: const Icon(Icons.fit_screen_outlined),
                    label: const Text('ملء الشريط'),
                  ),
                ],
                const SizedBox(height: 8),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final width = _referenceStepper(
                      label: _fullImageMode
                          ? 'عرض الصورة الكاملة'
                          : 'عرض الشعار',
                      icon: Icons.swap_horiz,
                      value: _fullImageMode
                          ? '${(_bannerWidth * 100).round()}%'
                          : '${_imageWidth.round()}',
                      onMinus: () => setState(
                        () => _fullImageMode
                            ? _bannerWidth = _bounded(
                                _bannerWidth - .05,
                                .2,
                                1.5,
                              )
                            : _imageWidth = _bounded(_imageWidth - 10, 40, 600),
                      ),
                      onPlus: () => setState(
                        () => _fullImageMode
                            ? _bannerWidth = _bounded(
                                _bannerWidth + .05,
                                .2,
                                1.5,
                              )
                            : _imageWidth = _bounded(_imageWidth + 10, 40, 600),
                      ),
                    );
                    final height = _referenceStepper(
                      label: _fullImageMode
                          ? 'ارتفاع الصورة الكاملة'
                          : 'ارتفاع الشعار',
                      icon: Icons.swap_vert,
                      value: _fullImageMode
                          ? '${(_bannerHeight * 100).round()}%'
                          : '${_imageHeight.round()}',
                      onMinus: () => setState(
                        () => _fullImageMode
                            ? _bannerHeight = _bounded(
                                _bannerHeight - .05,
                                .2,
                                1.5,
                              )
                            : _imageHeight = _bounded(
                                _imageHeight - 10,
                                30,
                                300,
                              ),
                      ),
                      onPlus: () => setState(
                        () => _fullImageMode
                            ? _bannerHeight = _bounded(
                                _bannerHeight + .05,
                                .2,
                                1.5,
                              )
                            : _imageHeight = _bounded(
                                _imageHeight + 10,
                                30,
                                300,
                              ),
                      ),
                    );
                    return constraints.maxWidth >= 320
                        ? Row(
                            children: [
                              Expanded(child: width),
                              const SizedBox(width: 8),
                              Expanded(child: height),
                            ],
                          )
                        : Column(
                            children: [
                              width,
                              const SizedBox(height: 8),
                              height,
                            ],
                          );
                  },
                ),
                const SizedBox(height: 8),
                _referencePosition(
                  label: 'موضع النص واتجاهه',
                  selected: _textAlign,
                  onChanged: (v) => setState(() => _textAlign = v),
                ),
                const SizedBox(height: 8),
                _referencePosition(
                  label: 'موضع الشعار',
                  selected: _logoPosition,
                  onChanged: (v) => setState(() {
                    _logoPosition = v;
                    _logoX = v == 'left'
                        ? .12
                        : v == 'right'
                        ? .88
                        : .5;
                  }),
                ),
              ],
            ),
          ),
        ],
      ),
    );
    void completeEditor(String action) {
      final result = _payload(action);
      if (widget.onCompleted != null) {
        widget.onCompleted!(result);
      } else {
        Navigator.pop(context, result);
      }
    }

    final bottomPreview = Material(
      color: const Color(0xFF061B39),
      elevation: 14,
      borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // الشريط الثابت هو مساحة التصميم نفسها، لذلك يجب أن يستقبل
              // سحب النص والصورة بدل أن يكون عرضًا ساكنًا فقط.
              _buildBannerPreview(showTools: true),
              const SizedBox(height: 6),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: () => completeEditor('save'),
                      icon: const Icon(Icons.save_outlined),
                      label: const Text('حفظ'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: FilledButton.icon(
                      onPressed: () {
                        if (_form.currentState!.validate())
                          completeEditor('submit');
                      },
                      icon: const Icon(Icons.send),
                      label: const Text('نشر'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        resizeToAvoidBottomInset: true,
        appBar: AppBar(
          title: const Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('الإعلانات'),
              Text(
                'أنشئ إعلانك بسهولة',
                style: TextStyle(fontSize: 13, color: Colors.white70),
              ),
            ],
          ),
          leading: IconButton(
            onPressed: () => Navigator.pop(context),
            icon: const Icon(Icons.arrow_back),
          ),
          actions: [
            Padding(
              padding: const EdgeInsetsDirectional.only(end: 14),
              child: Image.asset(
                'assets/images/khdoom_logo.png',
                width: 44,
                errorBuilder: (_, _, _) => const Icon(
                  Icons.campaign_outlined,
                  color: Color(0xFF38BDF8),
                ),
              ),
            ),
          ],
        ),
        body: Stack(
          children: [
            SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 340),
              keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
              child: editor,
            ),
            Positioned(left: 0, right: 0, bottom: 0, child: bottomPreview),
          ],
        ),
      ),
    );
  }

  Widget _referencePage(BuildContext context) {
    final designCard = _referenceCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextFormField(
            controller: _title,
            maxLength: 100,
            decoration: const InputDecoration(
              labelText: 'نص الإعلان',
              hintText: 'عرض خاص لأول 20 مشترك',
              prefixIcon: Icon(Icons.text_fields),
            ),
            onChanged: (_) => setState(() {}),
            validator: (s) =>
                s == null || s.trim().isEmpty ? 'اكتب عنوان الإعلان' : null,
          ),
          TextFormField(
            controller: _message,
            maxLength: 120,
            maxLines: 1,
            decoration: const InputDecoration(
              labelText: 'النص الإضافي (اختياري)',
              prefixIcon: Icon(Icons.subtitles_outlined),
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _fullImageMode
                    ? OutlinedButton.icon(
                        onPressed: () => setState(() => _fullImageMode = false),
                        icon: const Icon(Icons.text_fields, size: 18),
                        label: const Text('نص + شعار'),
                      )
                    : FilledButton.icon(
                        onPressed: () {},
                        icon: const Icon(Icons.text_fields, size: 18),
                        label: const Text('نص + شعار'),
                      ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _fullImageMode
                    ? FilledButton.icon(
                        onPressed: () {},
                        icon: const Icon(Icons.image_outlined, size: 18),
                        label: const Text('صورة كاملة'),
                      )
                    : OutlinedButton.icon(
                        onPressed: () => setState(() => _fullImageMode = true),
                        icon: const Icon(Icons.image_outlined, size: 18),
                        label: const Text('صورة كاملة'),
                      ),
              ),
            ],
          ),
          if (_fullImageMode) ...[
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: _pickingImage ? null : _pickFullBannerImage,
              icon: const Icon(Icons.upload_file_outlined),
              label: Text(
                _bannerImageData.isEmpty
                    ? 'رفع صورة الإعلان الكاملة'
                    : 'تغيير صورة الإعلان',
              ),
            ),
          ],
          LayoutBuilder(
            builder: (context, constraints) {
              final row = Row(
                children: [
                  Expanded(
                    child: _colorChooser(
                      title: 'لون الخط',
                      value: _textColor,
                      onChanged: (v) => setState(() => _textColor = v),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _referenceStepper(
                      label: 'حجم الخط',
                      icon: Icons.format_size,
                      value: '${_fontSize.round()}',
                      onMinus: () => setState(
                        () => _fontSize = _bounded(_fontSize - 1, 12, 32),
                      ),
                      onPlus: () => setState(
                        () => _fontSize = _bounded(_fontSize + 1, 12, 32),
                      ),
                    ),
                  ),
                ],
              );
              return constraints.maxWidth >= 260
                  ? row
                  : Column(
                      children: [
                        _colorChooser(
                          title: 'لون الخط',
                          value: _textColor,
                          onChanged: (v) => setState(() => _textColor = v),
                        ),
                        const SizedBox(height: 8),
                        _referenceStepper(
                          label: 'حجم الخط',
                          icon: Icons.format_size,
                          value: '${_fontSize.round()}',
                          onMinus: () => setState(
                            () => _fontSize = _bounded(_fontSize - 1, 12, 32),
                          ),
                          onPlus: () => setState(
                            () => _fontSize = _bounded(_fontSize + 1, 12, 32),
                          ),
                        ),
                      ],
                    );
            },
          ),
          const Divider(height: 20, color: Color(0xFF204A80)),
          LayoutBuilder(
            builder: (context, constraints) {
              final logo = _imageData.isNotEmpty
                  ? ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Image.memory(
                        base64Decode(_imageData.split(',').last),
                        width: 36,
                        height: 32,
                        fit: BoxFit.contain,
                      ),
                    )
                  : Image.asset(
                      'assets/images/khdoom_logo.png',
                      width: 36,
                      height: 32,
                      fit: BoxFit.contain,
                    );
              final changeLogo = OutlinedButton.icon(
                onPressed: _pickingImage ? null : _pickImage,
                icon: const Icon(Icons.image_outlined, size: 18),
                label: const Text('تغيير الشعار'),
              );
              final logoControl = constraints.maxWidth < 330
                  ? Column(
                      children: [
                        Row(
                          children: [
                            logo,
                            const SizedBox(width: 6),
                            const Text('الشعار'),
                          ],
                        ),
                        const SizedBox(height: 8),
                        SizedBox(width: double.infinity, child: changeLogo),
                      ],
                    )
                  : Row(
                      children: [
                        logo,
                        const SizedBox(width: 6),
                        const Expanded(child: Text('الشعار')),
                        changeLogo,
                      ],
                    );
              final barControl = _colorChooser(
                title: 'لون الشريط',
                value: _barColor,
                onChanged: (v) => setState(() => _barColor = v),
              );
              return constraints.maxWidth >= 260
                  ? Row(
                      children: [
                        Expanded(child: barControl),
                        const SizedBox(width: 12),
                        Expanded(child: logoControl),
                      ],
                    )
                  : Column(
                      children: [
                        barControl,
                        const SizedBox(height: 8),
                        logoControl,
                      ],
                    );
            },
          ),
          const Divider(height: 20, color: Color(0xFF204A80)),
          _referenceStepper(
            label: 'حجم الشعار',
            icon: Icons.open_in_full,
            value: '$_logoSize',
            onMinus: () => setState(
              () => _logoScale = _bounded(_logoScale - 4 / 72, .5, 1.5),
            ),
            onPlus: () => setState(
              () => _logoScale = _bounded(_logoScale + 4 / 72, .5, 1.5),
            ),
          ),
          const SizedBox(height: 10),
          LayoutBuilder(
            builder: (context, constraints) {
              final logoPosition = _referencePosition(
                label: 'موضع الشعار',
                selected: _logoPosition,
                onChanged: (v) => setState(() {
                  _logoPosition = v;
                  _logoX = v == 'left'
                      ? .12
                      : v == 'right'
                      ? .88
                      : .5;
                }),
              );
              final textPosition = _referencePosition(
                label: 'موضع النص',
                selected: _textAlign,
                onChanged: (v) => setState(() {
                  _textAlign = v;
                  _textX = v == 'left'
                      ? .2
                      : v == 'right'
                      ? .8
                      : .5;
                }),
              );
              return constraints.maxWidth >= 260
                  ? Row(
                      children: [
                        Expanded(child: logoPosition),
                        const SizedBox(width: 10),
                        Expanded(child: textPosition),
                      ],
                    )
                  : Column(children: [logoPosition, textPosition]);
            },
          ),
          const SizedBox(height: 8),
          _referenceStepper(
            label: 'مدة العرض بالثواني',
            icon: Icons.timer_outlined,
            value: '$_displaySeconds',
            onMinus: () => setState(
              () => _displaySeconds = (_displaySeconds - 1).clamp(3, 60),
            ),
            onPlus: () => setState(
              () => _displaySeconds = (_displaySeconds + 1).clamp(3, 60),
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _chooseFont,
            icon: const Icon(Icons.font_download_outlined),
            label: Text(
              _fontFamily == 'default' ? 'اختيار الخط' : 'الخط: $_fontFamily',
            ),
          ),
        ],
      ),
    );
    final settings = Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.add_circle_outline),
                  label: const Text('إنشاء إعلان'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.receipt_long_outlined),
                  label: const Text('المنشور'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          designCard,
          const SizedBox(height: 14),
          _referenceCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'معاينة مباشرة',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 4),
                const Text(
                  'يتم تحديث المعاينة تلقائيًا مع أي تغيير في الإعدادات',
                  style: TextStyle(color: Colors.white70, fontSize: 12),
                ),
                const SizedBox(height: 10),
                _buildBannerPreview(showTools: false),
              ],
            ),
          ),
          const SizedBox(height: 14),
          LayoutBuilder(
            builder: (context, constraints) {
              final preview = OutlinedButton.icon(
                onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('المعاينة محدثة بالأسفل')),
                ),
                icon: const Icon(Icons.visibility_outlined),
                label: const Text('معاينة'),
              );
              final save = OutlinedButton.icon(
                onPressed: () => Navigator.pop(context, _payload('save')),
                icon: const Icon(Icons.save_outlined),
                label: const Text('حفظ'),
              );
              final publish = FilledButton.icon(
                onPressed: () {
                  if (_form.currentState!.validate())
                    Navigator.pop(context, _payload('submit'));
                },
                icon: const Icon(Icons.send),
                label: const Text('نشر'),
              );
              if (constraints.maxWidth < 360) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    preview,
                    const SizedBox(height: 8),
                    save,
                    const SizedBox(height: 8),
                    publish,
                  ],
                );
              }
              return Row(
                children: [
                  Expanded(child: preview),
                  const SizedBox(width: 8),
                  Expanded(child: save),
                  const SizedBox(width: 8),
                  Expanded(child: publish),
                ],
              );
            },
          ),
          const SizedBox(height: 10),
          TextFormField(
            controller: _contact,
            maxLength: 80,
            decoration: const InputDecoration(
              labelText: 'وسيلة التواصل (اختياري)',
            ),
          ),
        ],
      ),
    );
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        resizeToAvoidBottomInset: true,
        appBar: AppBar(
          title: const Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('الإعلانات'),
              Text(
                'أنشئ إعلاناتك بسهولة',
                style: TextStyle(fontSize: 13, color: Colors.white70),
              ),
            ],
          ),
          leading: IconButton(
            onPressed: () => Navigator.pop(context),
            icon: const Icon(Icons.arrow_back),
          ),
          actions: [
            Padding(
              padding: const EdgeInsetsDirectional.only(end: 14),
              child: Image.asset(
                'assets/images/khdoom_logo.png',
                width: 44,
                errorBuilder: (_, _, _) => const Icon(
                  Icons.campaign_outlined,
                  color: Color(0xFF38BDF8),
                ),
              ),
            ),
          ],
        ),
        body: SafeArea(
          child: SingleChildScrollView(
            keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
            padding: EdgeInsets.fromLTRB(
              12,
              10,
              12,
              24 + MediaQuery.viewInsetsOf(context).bottom,
            ),
            child: settings,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (widget.fullScreen) return _newMobileEditorPage(context);
    final editor = Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.add_circle_outline),
                  label: const Text('إنشاء إعلان'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.receipt_long_outlined),
                  label: const Text('المنشور'),
                ),
              ),
            ],
          ),
          if (!widget.fullScreen) ...[
            const SizedBox(height: 10),
            DropdownButtonFormField<int>(
              initialValue: _days,
              decoration: const InputDecoration(labelText: 'المدة المطلوبة'),
              items: [
                for (var i = 1; i <= 10; i++)
                  DropdownMenuItem(value: i, child: Text('$i يوم')),
              ],
              onChanged: (v) => setState(() => _days = v ?? 10),
            ),
          ],
          const SizedBox(height: 14),
          _settingCard(
            child: TextFormField(
              controller: _title,
              maxLength: 100,
              decoration: const InputDecoration(
                labelText: 'نص الإعلان',
                hintText: 'عرض خاص لأول 20 مشترك',
                prefixIcon: Icon(Icons.text_fields),
              ),
              onChanged: (_) => setState(() {}),
              validator: (s) =>
                  s == null || s.trim().isEmpty ? 'اكتب عنوان الإعلان' : null,
            ),
          ),
          _settingCard(
            child: _colorChooser(
              title: 'لون الخط',
              value: _textColor,
              onChanged: (v) => _textColor = v,
            ),
          ),
          _stepper(
            label: 'حجم الخط',
            icon: Icons.format_size,
            value: '${_fontSize.round()}',
            onMinus: () =>
                setState(() => _fontSize = _bounded(_fontSize - 1, 12, 32)),
            onPlus: () =>
                setState(() => _fontSize = _bounded(_fontSize + 1, 12, 32)),
          ),
          _settingCard(
            child: OutlinedButton.icon(
              onPressed: _chooseFont,
              icon: const Icon(Icons.font_download_outlined),
              label: Text(
                _fontFamily == 'default'
                    ? 'اختيار الخط: الافتراضي'
                    : 'اختيار الخط: $_fontFamily',
              ),
            ),
          ),
          _settingCard(
            child: _colorChooser(
              title: 'لون الشريط',
              value: _barColor,
              onChanged: (v) => _barColor = v,
            ),
          ),
          _settingCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('الشعار'),
                const SizedBox(height: 6),
                Row(
                  children: [
                    if (_imageData.isNotEmpty)
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Image.memory(
                          base64Decode(_imageData.split(',').last),
                          width: 48,
                          height: 40,
                          fit: BoxFit.cover,
                        ),
                      )
                    else
                      const Icon(
                        Icons.campaign_outlined,
                        color: Color(0xFF38BDF8),
                        size: 38,
                      ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _pickingImage ? null : _pickImage,
                        icon: const Icon(Icons.image_outlined),
                        label: const Text('تغيير الشعار'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          _stepper(
            label: 'حجم الشعار',
            icon: Icons.open_in_full,
            value: '$_logoSize',
            onMinus: () => setState(
              () => _logoScale = _bounded(_logoScale - 4 / 72, .5, 1.5),
            ),
            onPlus: () => setState(
              () => _logoScale = _bounded(_logoScale + 4 / 72, .5, 1.5),
            ),
          ),
          _positionSelector(
            label: 'موضع الشعار',
            selected: _logoPosition,
            onChanged: (v) {
              setState(() {
                _logoPosition = v;
                _logoX = v == 'left'
                    ? .12
                    : v == 'right'
                    ? .88
                    : .5;
              });
            },
          ),
          _positionSelector(
            label: 'موضع النص',
            selected: _textAlign,
            onChanged: (v) {
              setState(() {
                _textAlign = v;
                _textX = v == 'left'
                    ? .2
                    : v == 'right'
                    ? .8
                    : .5;
              });
            },
          ),
          _stepper(
            label: 'مدة العرض بالثواني',
            icon: Icons.timer_outlined,
            value: '$_displaySeconds',
            onMinus: () => setState(
              () => _displaySeconds = (_displaySeconds - 1).clamp(3, 60),
            ),
            onPlus: () => setState(
              () => _displaySeconds = (_displaySeconds + 1).clamp(3, 60),
            ),
          ),
          // مدة الطلب تبقى ضمن نموذج المراجعة القديم حتى لا تتأثر الطلبات
          // الحالية، بينما مدة ظهور الشريط يتحكم بها الحقل السابق.
          const SizedBox(height: 4),
          _buildBannerPreview(),
          const SizedBox(height: 8),
          TextFormField(
            controller: _message,
            maxLength: 120,
            maxLines: 2,
            decoration: const InputDecoration(
              labelText: 'النص الفرعي (اختياري)',
            ),
            onChanged: (_) => setState(() {}),
          ),
          TextFormField(
            controller: _contact,
            maxLength: 80,
            decoration: const InputDecoration(
              labelText: 'وسيلة التواصل (اختياري)',
            ),
          ),
          const SizedBox(height: 4),
          const Text(
            'تظهر التعديلات مباشرة في شريط المعاينة قبل الحفظ أو الإرسال.',
          ),
        ],
      ),
    );
    final actions = Row(
      children: [
        if (!widget.fullScreen) ...[
          Expanded(
            child: TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('إلغاء'),
            ),
          ),
          const SizedBox(width: 4),
        ],
        Expanded(
          child: FilledButton.icon(
            onPressed: () {
              if (_form.currentState!.validate())
                Navigator.pop(context, _payload('submit'));
            },
            icon: const Icon(Icons.send),
            label: const Text('إرسال للمراجعة'),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: OutlinedButton.icon(
            onPressed: () => Navigator.pop(context, _payload('save')),
            icon: const Icon(Icons.save_outlined),
            label: const Text('حفظ'),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: OutlinedButton.icon(
            onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('المعاينة محدثة بالأسفل')),
            ),
            icon: const Icon(Icons.visibility_outlined),
            label: const Text('معاينة'),
          ),
        ),
      ],
    );
    final page = Scaffold(
      resizeToAvoidBottomInset: true,
      appBar: AppBar(
        title: const Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text('الإعلانات'),
            Text(
              'أنشئ إعلاناتك بسهولة',
              style: TextStyle(fontSize: 13, color: Colors.white70),
            ),
          ],
        ),
        leading: IconButton(
          onPressed: () => Navigator.pop(context),
          icon: const Icon(Icons.arrow_back),
        ),
        actions: [
          Padding(
            padding: const EdgeInsetsDirectional.only(end: 16),
            child: Image.asset(
              'assets/images/khdoom_logo.png',
              width: 42,
              errorBuilder: (_, _, _) =>
                  const Icon(Icons.campaign_outlined, color: Color(0xFF38BDF8)),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
          padding: EdgeInsets.fromLTRB(
            16,
            12,
            16,
            20 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: editor,
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: AnimatedPadding(
          duration: const Duration(milliseconds: 180),
          padding: EdgeInsets.fromLTRB(
            12,
            8,
            12,
            12 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: actions,
        ),
      ),
    );
    final dialog = AlertDialog(
      title: const Text('طلب إعلان للمراجعة'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(child: editor),
      ),
      actions: [actions],
    );
    return Directionality(
      textDirection: TextDirection.rtl,
      child: widget.fullScreen ? page : dialog,
    );
  }
}
