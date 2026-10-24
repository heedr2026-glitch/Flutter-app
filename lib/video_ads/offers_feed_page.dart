import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:video_player/video_player.dart';

import 'create_video_ad_page.dart';
import 'my_video_ads_page.dart';
import '../khdoom_dark_page.dart';
import 'video_ads_api.dart';

const _savedKey = 'video_ads_saved_ids';

/// صفحة «عروض»: فيديوهات إعلانات المؤسسات بملء الشاشة، تسحب للأعلى للي بعده.
class OffersFeedPage extends StatefulWidget {
  const OffersFeedPage({super.key, this.active = true, this.savedOnly = false});

  /// تتوقف الفيديوهات لما المستخدم يطلع من تبويب العروض.
  final bool active;
  final bool savedOnly;

  @override
  State<OffersFeedPage> createState() => _OffersFeedPageState();
}

class _OffersFeedPageState extends State<OffersFeedPage> {
  // الحوارات تاخذ ألوان الصفحة الداكنة من هنا.
  final _darkKey = GlobalKey();
  VideoAdsApi? _api;
  List<Map<String, dynamic>> _items = [];
  Set<String> _saved = {};
  String _activity = '';
  List<String> _activities = [];
  bool _loading = true;
  String? _error;
  int _index = 0;
  final _pages = PageController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = _api ??= await VideoAdsApi.create();
      final prefs = await SharedPreferences.getInstance();
      _saved = (prefs.getStringList(_savedKey) ?? const []).toSet();
      var items = await api.feed(activity: _activity);
      if (widget.savedOnly) {
        items = items.where((x) => _saved.contains('${x['id']}')).toList();
      }
      if (_activity.isEmpty) {
        _activities = {
          for (final x in items)
            if ((x['activity'] ?? '').toString().trim().isNotEmpty)
              x['activity'].toString().trim(),
        }.toList()..sort();
      }
      if (!mounted) return;
      setState(() {
        _items = items;
        _index = 0;
        _loading = false;
      });
      if (_pages.hasClients) _pages.jumpToPage(0);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.toString();
        _loading = false;
      });
    }
  }

  Future<void> _toggleSave(Map<String, dynamic> item) async {
    final id = '${item['id']}';
    final prefs = await SharedPreferences.getInstance();
    setState(() => _saved.contains(id) ? _saved.remove(id) : _saved.add(id));
    await prefs.setStringList(_savedKey, _saved.toList());
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          _saved.contains(id) ? 'انحفظ في المحفوظات 🔖' : 'انشال من المحفوظات',
        ),
      ),
    );
  }

  Future<void> _like(Map<String, dynamic> item) async {
    final wasLiked = item['liked'] == true;
    final before = (item['likes'] as num?)?.toInt() ?? 0;
    // يتغير فورًا على الشاشة، ثم نثبته من السيرفر.
    setState(() {
      item['liked'] = !wasLiked;
      item['likes'] = before + (wasLiked ? -1 : 1);
    });
    try {
      final result = await _api!.toggleLike((item['id'] as num).toInt());
      if (!mounted) return;
      setState(() {
        item['liked'] = result['liked'] == true;
        item['likes'] = (result['likes'] as num?)?.toInt() ?? item['likes'];
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        item['liked'] = wasLiked;
        item['likes'] = before;
      });
      _message(error.toString());
    }
  }

  Future<void> _contact(Map<String, dynamic> item) async {
    final phone = (item['contact'] ?? '').toString().trim();
    if (phone.isEmpty) {
      _message('المعلن ما أضاف رقم تواصل');
      return;
    }
    final choice = await showModalBottomSheet<String>(
      context: _darkKey.currentContext ?? context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              title: Text(
                item['organizationName']?.toString() ?? 'المعلن',
                style: const TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.call),
              title: const Text('اتصال'),
              onTap: () => Navigator.pop(context, 'call'),
            ),
            ListTile(
              leading: const Icon(Icons.chat, color: Colors.green),
              title: const Text('واتساب'),
              onTap: () => Navigator.pop(context, 'whatsapp'),
            ),
          ],
        ),
      ),
    );
    if (choice == null) return;
    final title = (item['title'] ?? '').toString();
    final text =
        'السلام عليكم، شفت إعلانكم في خدوم${title.isEmpty ? '' : ' «$title»'}';
    final uri = choice == 'call'
        ? Uri(scheme: 'tel', path: phone)
        : Uri.parse(
            'https://wa.me/${whatsappNumber(phone)}?text=${Uri.encodeComponent(text)}',
          );
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
      _message('تعذر فتح ${choice == 'call' ? 'الاتصال' : 'واتساب'}');
    }
  }

  Future<void> _download(Map<String, dynamic> item) async {
    try {
      final link = await _api!.downloadLink((item['id'] as num).toInt());
      if (!await launchUrl(link, mode: LaunchMode.externalApplication)) {
        _message('تعذر بدء التنزيل');
      } else {
        _message('بدأ التنزيل… الفيديو عليه شعار خدوم');
      }
    } catch (error) {
      _message(error.toString());
    }
  }

  Future<void> _share(Map<String, dynamic> item) async {
    final text =
        'شف عرض ${item['organizationName'] ?? ''} في تطبيق خدوم 🎬 ${item['title'] ?? ''}';
    await launchUrl(
      Uri.parse('https://wa.me/?text=${Uri.encodeComponent(text)}'),
      mode: LaunchMode.externalApplication,
    );
  }

  void _message(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _openCreate() async {
    final created = await Navigator.push<bool>(
      context,
      MaterialPageRoute(builder: (_) => const CreateVideoAdPage()),
    );
    if (created == true) _message('انرسل إعلانك للمراجعة ✅');
  }

  @override
  Widget build(BuildContext context) {
    return KhdoomDarkPage(
      child: Directionality(
      key: _darkKey,
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: Colors.black,
        extendBodyBehindAppBar: true,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          foregroundColor: Colors.white,
          elevation: 0,
          automaticallyImplyLeading: widget.savedOnly,
          title: Text(widget.savedOnly ? 'المحفوظات' : 'عروض'),
          actions: [
            if (!widget.savedOnly) ...[
              IconButton(
                tooltip: 'المحفوظات',
                icon: const Icon(Icons.bookmarks_outlined),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const OffersFeedPage(savedOnly: true),
                  ),
                ),
              ),
              IconButton(
                tooltip: 'إعلاناتي',
                icon: const Icon(Icons.video_library_outlined),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const MyVideoAdsPage()),
                ),
              ),
              IconButton(
                tooltip: 'أعلن بالفيديو',
                icon: const Icon(Icons.add_circle_outline, size: 30),
                onPressed: _openCreate,
              ),
            ],
          ],
          bottom: widget.savedOnly || _activities.isEmpty
              ? null
              : PreferredSize(
                  preferredSize: const Size.fromHeight(44),
                  child: SizedBox(
                    height: 44,
                    child: ListView(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      children: [
                        for (final a in ['', ..._activities])
                          Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 4),
                            child: ChoiceChip(
                              label: Text(a.isEmpty ? 'الكل' : a),
                              selected: _activity == a,
                              onSelected: (_) {
                                setState(() => _activity = a);
                                _load();
                              },
                            ),
                          ),
                      ],
                    ),
                  ),
                ),
        ),
        body: _body(),
      ),
    ),
    );
  }

  Widget _body() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(color: Colors.white));
    }
    if (_error != null) {
      return _centerMessage(_error!, retry: true);
    }
    if (_items.isEmpty) {
      return _centerMessage(
        widget.savedOnly
            ? 'ما حفظت أي إعلان بعد. اضغط 🔖 على أي فيديو يعجبك.'
            : 'ما فيه عروض الحين. كن أول من يعلن 🎬',
        retry: !widget.savedOnly,
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: PageView.builder(
        controller: _pages,
        scrollDirection: Axis.vertical,
        itemCount: _items.length,
        onPageChanged: (i) => setState(() => _index = i),
        itemBuilder: (context, i) {
          final item = _items[i];
          return _VideoAdView(
            key: ValueKey(item['id']),
            api: _api!,
            item: item,
            playing: widget.active && i == _index,
            saved: _saved.contains('${item['id']}'),
            onSave: () => _toggleSave(item),
            onLike: () => _like(item),
            onContact: () => _contact(item),
            onShare: () => _share(item),
            onDownload: item['allowDownload'] == true
                ? () => _download(item)
                : null,
          );
        },
      ),
    );
  }

  Widget _centerMessage(String text, {bool retry = false}) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white, fontSize: 16),
          ),
          if (retry) ...[
            const SizedBox(height: 16),
            OutlinedButton(
              onPressed: _load,
              style: OutlinedButton.styleFrom(foregroundColor: Colors.white),
              child: const Text('تحديث'),
            ),
          ],
        ],
      ),
    ),
  );
}

class _VideoAdView extends StatefulWidget {
  const _VideoAdView({
    super.key,
    required this.api,
    required this.item,
    required this.playing,
    required this.saved,
    required this.onSave,
    required this.onLike,
    required this.onContact,
    required this.onShare,
    this.onDownload,
  });

  final VideoAdsApi api;
  final Map<String, dynamic> item;
  final bool playing;
  final bool saved;
  final VoidCallback onSave;
  final VoidCallback onLike;
  final VoidCallback onContact;
  final VoidCallback onShare;
  final VoidCallback? onDownload;

  @override
  State<_VideoAdView> createState() => _VideoAdViewState();
}

class _VideoAdViewState extends State<_VideoAdView> {
  VideoPlayerController? _controller;
  bool _muted = true;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    if (widget.playing) _start();
  }

  @override
  void didUpdateWidget(covariant _VideoAdView old) {
    super.didUpdateWidget(old);
    if (widget.playing && _controller == null) {
      _start();
    } else if (widget.playing) {
      _controller?.play();
    } else {
      _controller?.pause();
    }
  }

  Future<void> _start() async {
    final controller = VideoPlayerController.networkUrl(
      widget.api.resolve(widget.item['videoUrl'].toString()),
      httpHeaders: widget.api.authHeaders,
    );
    _controller = controller;
    try {
      await controller.initialize();
      await controller.setLooping(true);
      await controller.setVolume(_muted ? 0 : 1);
      if (!mounted) return;
      if (widget.playing) await controller.play();
      setState(() {});
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  void _toggleSound() {
    setState(() => _muted = !_muted);
    _controller?.setVolume(_muted ? 0 : 1);
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final controller = _controller;
    final ready = controller != null && controller.value.isInitialized;
    final overlay = (item['overlayText'] ?? '').toString().trim();
    final overlayY = ((item['overlayY'] as num?)?.toDouble() ?? 0.15).clamp(0.05, 0.85);
    return GestureDetector(
      onTap: _toggleSound,
      onDoubleTap: item['liked'] == true ? null : widget.onLike,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Container(color: Colors.black),
          if (ready)
            Center(
              child: AspectRatio(
                aspectRatio: controller.value.aspectRatio,
                child: VideoPlayer(controller),
              ),
            )
          else
            Center(
              child: _failed
                  ? const Text(
                      'تعذر تشغيل الفيديو',
                      style: TextStyle(color: Colors.white70),
                    )
                  : const CircularProgressIndicator(color: Colors.white),
            ),
          if (overlay.isNotEmpty)
            Align(
              alignment: Alignment(0, overlayY * 2 - 1),
              child: _OverlayText(overlay),
            ),
          if (ready && _muted)
            const Positioned(
              top: 110,
              left: 16,
              child: Icon(Icons.volume_off, color: Colors.white70),
            ),
          Positioned(
            left: 10,
            bottom: 110,
            child: Column(
              children: [
                _SideButton(
                  icon: item['liked'] == true ? Icons.favorite : Icons.favorite_border,
                  color: item['liked'] == true ? Colors.redAccent : Colors.white,
                  label: '${(item['likes'] as num?)?.toInt() ?? 0}',
                  onTap: widget.onLike,
                ),
                _SideButton(
                  icon: widget.saved ? Icons.bookmark : Icons.bookmark_border,
                  label: 'حفظ',
                  onTap: widget.onSave,
                ),
                _SideButton(icon: Icons.share, label: 'مشاركة', onTap: widget.onShare),
                if (widget.onDownload != null)
                  _SideButton(
                    icon: Icons.download,
                    label: 'تنزيل',
                    onTap: widget.onDownload!,
                  ),
              ],
            ),
          ),
          Positioned(
            right: 16,
            left: 80,
            bottom: 24,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${item['organizationName'] ?? ''}${item['vip'] == true ? ' ⭐' : ''}',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 17,
                    fontWeight: FontWeight.bold,
                    shadows: [Shadow(blurRadius: 6)],
                  ),
                ),
                if ((item['title'] ?? '').toString().isNotEmpty)
                  Text(
                    item['title'].toString(),
                    style: const TextStyle(color: Colors.white, shadows: [Shadow(blurRadius: 6)]),
                  ),
                if ((item['caption'] ?? '').toString().isNotEmpty)
                  Text(
                    item['caption'].toString(),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white70, shadows: [Shadow(blurRadius: 6)]),
                  ),
                const SizedBox(height: 10),
                FilledButton.icon(
                  onPressed: widget.onContact,
                  icon: const Icon(Icons.phone),
                  label: const Text('تواصل'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _OverlayText extends StatelessWidget {
  const _OverlayText(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Container(
    margin: const EdgeInsets.symmetric(horizontal: 24),
    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
    decoration: BoxDecoration(
      color: Colors.black54,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Text(
      text,
      textAlign: TextAlign.center,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
      style: const TextStyle(
        color: Colors.white,
        fontSize: 22,
        fontWeight: FontWeight.bold,
      ),
    ),
  );
}

class _SideButton extends StatelessWidget {
  const _SideButton({
    required this.icon,
    required this.label,
    required this.onTap,
    this.color = Colors.white,
  });
  final IconData icon;
  final Color color;
  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: InkWell(
      onTap: onTap,
      child: Column(
        children: [
          Icon(icon, color: color, size: 32, shadows: const [Shadow(blurRadius: 6)]),
          Text(label, style: const TextStyle(color: Colors.white, fontSize: 12)),
        ],
      ),
    ),
  );
}

/// نص على الفيديو يستخدم نفس الشكل في المعاينة.
Widget overlayTextPreview(String text) => _OverlayText(text);
