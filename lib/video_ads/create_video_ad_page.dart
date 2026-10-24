import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:video_player/video_player.dart';

import '../main.dart' show SubscriptionPackagesPage;
import 'offers_feed_page.dart' show overlayTextPreview;
import '../khdoom_dark_page.dart';
import 'video_ads_api.dart';

/// إنشاء إعلان فيديو: تصوير أو اختيار من المعرض ← قص ← نص (اختياري) ← إرسال للمراجعة.
class CreateVideoAdPage extends StatefulWidget {
  const CreateVideoAdPage({super.key});

  @override
  State<CreateVideoAdPage> createState() => _CreateVideoAdPageState();
}

class _CreateVideoAdPageState extends State<CreateVideoAdPage> {
  // الحوارات تاخذ ألوان الصفحة الداكنة من هنا.
  final _darkKey = GlobalKey();
  VideoAdsApi? _api;
  Map<String, dynamic>? _quota;
  bool _pay = false;
  String? _error;

  File? _file;
  VideoPlayerController? _player;
  RangeValues _range = const RangeValues(0, 0);
  double _duration = 0;

  final _title = TextEditingController();
  final _caption = TextEditingController();
  final _overlay = TextEditingController();
  final _activity = TextEditingController();
  final _contact = TextEditingController();
  final _transfer = TextEditingController();
  double _overlayY = 0.15;
  bool _allowDownload = true;
  bool _sending = false;
  double _progress = 0;

  int get _maxSeconds => (_quota?['max_seconds'] as num?)?.toInt() ?? 30;

  @override
  void initState() {
    super.initState();
    _prepare();
  }

  @override
  void dispose() {
    _player?.dispose();
    for (final c in [_title, _caption, _overlay, _activity, _contact, _transfer]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _prepare() async {
    try {
      final api = _api ??= await VideoAdsApi.create();
      final quota = await api.quota();
      if (!mounted) return;
      setState(() => _quota = quota);
      if (quota['enabled'] == false) {
        setState(() => _error = 'إعلانات الفيديو موقفة مؤقتًا من الإدارة');
        return;
      }
      final freeLeft =
          quota['can_create'] == true && ((quota['remaining'] as num?) ?? 0) > 0;
      if (!freeLeft) await _askUpgradeOrPay();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
  }

  Future<void> _askUpgradeOrPay() async {
    final quota = _quota!;
    final vip = quota['can_create'] == true;
    final price = (quota['paid_price_sar'] as num?) ?? 29;
    final choice = await showDialog<String>(
      context: _darkKey.currentContext ?? context,
      barrierDismissible: false,
      builder: (context) => Directionality(
        textDirection: TextDirection.rtl,
        child: AlertDialog(
          title: const Text('🎬 أعلن بالفيديو'),
          content: Text(
            vip
                ? 'خلصت إعلاناتك المجانية لهذا الشهر (${quota['used']} من ${quota['limit']}).\nتقدر تنشر إعلان إضافي بـ ${price.toStringAsFixed(0)} ريال.'
                : 'إعلانات الفيديو مجانية لمشتركي VIP ⭐\nاشترك في VIP، أو انشر إعلان واحد بـ ${price.toStringAsFixed(0)} ريال.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, 'cancel'),
              child: const Text('إلغاء'),
            ),
            if (!vip)
              TextButton(
                onPressed: () => Navigator.pop(context, 'upgrade'),
                child: const Text('ترقية للـ VIP ⭐'),
              ),
            FilledButton(
              onPressed: () => Navigator.pop(context, 'pay'),
              child: Text('💳 إعلان بـ ${price.toStringAsFixed(0)} ريال'),
            ),
          ],
        ),
      ),
    );
    if (!mounted) return;
    if (choice == 'pay') {
      setState(() => _pay = true);
    } else if (choice == 'upgrade') {
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => const SubscriptionPackagesPage()),
      );
    } else {
      Navigator.pop(context);
    }
  }

  Future<void> _pick(ImageSource source) async {
    final picked = await ImagePicker().pickVideo(
      source: source,
      maxDuration: source == ImageSource.camera
          ? Duration(seconds: _maxSeconds)
          : null,
    );
    if (picked == null) return;
    final file = File(picked.path);
    final player = VideoPlayerController.file(file);
    try {
      await player.initialize();
    } catch (_) {
      await player.dispose();
      _message('تعذر فتح الفيديو، جرّب فيديو ثاني');
      return;
    }
    await _player?.dispose();
    final seconds = player.value.duration.inMilliseconds / 1000;
    await player.setLooping(false);
    player.addListener(_loopInsideRange);
    await player.play();
    if (!mounted) return;
    setState(() {
      _file = file;
      _player = player;
      _duration = seconds;
      _range = RangeValues(0, seconds.clamp(0, _maxSeconds.toDouble()));
    });
  }

  void _loopInsideRange() {
    final player = _player;
    if (player == null || !player.value.isInitialized) return;
    final position = player.value.position.inMilliseconds / 1000;
    if (position >= _range.end || !player.value.isPlaying && position >= _duration - 0.1) {
      player.seekTo(Duration(milliseconds: (_range.start * 1000).round()));
      player.play();
    }
  }

  void _onRange(RangeValues values) {
    var start = values.start;
    var end = values.end;
    // ما يتجاوز المدة القصوى: نحرك الطرف الثاني مع اللي يتحرك.
    if (end - start > _maxSeconds) {
      if (start != _range.start) {
        end = start + _maxSeconds;
      } else {
        start = end - _maxSeconds;
      }
    }
    if (end - start < 1) return;
    setState(() => _range = RangeValues(start, end));
    _player?.seekTo(Duration(milliseconds: (start * 1000).round()));
  }

  Future<void> _send() async {
    final file = _file;
    if (file == null) return;
    if (_pay && _transfer.text.trim().isEmpty) {
      _message('اكتب الاسم اللي حوّلت منه المبلغ');
      return;
    }
    setState(() {
      _sending = true;
      _progress = 0;
    });
    try {
      final api = _api!;
      final needsTrim = _duration > _maxSeconds + 0.5 || _range.start > 0.2 || _range.end < _duration - 0.2;
      final length = _range.end - _range.start;
      final id = await api.createDraft({
        'title': _title.text.trim(),
        'caption': _caption.text.trim(),
        'overlayText': _overlay.text.trim(),
        'overlayY': _overlayY,
        'activity': _activity.text.trim(),
        'contact': _contact.text.trim(),
        'allowDownload': _allowDownload,
        'seconds': needsTrim ? length : _duration,
        'contentType': contentTypeFor(file.path),
      });
      await api.upload(
        id,
        file,
        contentType: contentTypeFor(file.path),
        start: needsTrim ? _range.start : null,
        end: needsTrim ? _range.end : null,
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
      );
      await api.submit(id, pay: _pay, transferName: _transfer.text.trim());
      if (!mounted) return;
      Navigator.pop(context, true);
    } catch (error) {
      _message(error.toString());
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  void _message(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  String _time(double seconds) {
    final s = seconds.round();
    return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return KhdoomDarkPage(
      child: Directionality(
      key: _darkKey,
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(title: const Text('إعلان فيديو جديد')),
        body: _error != null
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(_error!, textAlign: TextAlign.center),
                ),
              )
            : _quota == null
            ? const Center(child: CircularProgressIndicator())
            : _file == null
            ? _chooseSource()
            : _editor(),
      ),
    ),
    );
  }

  Widget _chooseSource() => Padding(
    padding: const EdgeInsets.all(24),
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!_pay && _quota!['can_create'] == true)
          Text(
            'باقي لك ${_quota!['remaining']} من ${_quota!['limit']} إعلانات هذا الشهر',
            textAlign: TextAlign.center,
            style: const TextStyle(fontWeight: FontWeight.bold),
          ),
        const SizedBox(height: 8),
        Text(
          'المدة القصوى للفيديو $_maxSeconds ثانية. إذا الفيديو أطول تقدر تقصه.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 28),
        FilledButton.icon(
          onPressed: () => _pick(ImageSource.camera),
          icon: const Icon(Icons.videocam),
          label: const Padding(
            padding: EdgeInsets.all(14),
            child: Text('صوّر الحين'),
          ),
        ),
        const SizedBox(height: 14),
        OutlinedButton.icon(
          onPressed: () => _pick(ImageSource.gallery),
          icon: const Icon(Icons.photo_library),
          label: const Padding(
            padding: EdgeInsets.all(14),
            child: Text('اختر من المعرض'),
          ),
        ),
      ],
    ),
  );

  Widget _editor() {
    final player = _player!;
    final overlay = _overlay.text.trim();
    final length = _range.end - _range.start;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        AspectRatio(
          aspectRatio: 9 / 16,
          child: LayoutBuilder(
            builder: (context, box) => GestureDetector(
              onVerticalDragUpdate: overlay.isEmpty
                  ? null
                  : (d) => setState(
                      () => _overlayY = (_overlayY + d.delta.dy / box.maxHeight).clamp(0.05, 0.85),
                    ),
              child: Container(
                color: Colors.black,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    Center(
                      child: AspectRatio(
                        aspectRatio: player.value.aspectRatio,
                        child: VideoPlayer(player),
                      ),
                    ),
                    if (overlay.isNotEmpty)
                      Align(
                        alignment: Alignment(0, _overlayY * 2 - 1),
                        child: overlayTextPreview(overlay),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
        if (overlay.isNotEmpty)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text(
              'اسحب على الفيديو للأعلى أو الأسفل عشان تحرك النص',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 12),
            ),
          ),
        const SizedBox(height: 12),
        Text(
          '✂️ القص: ${_time(_range.start)} ← ${_time(_range.end)}  (المدة: ${length.round()} ثانية من $_maxSeconds)',
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        if (_duration > 0.5)
          RangeSlider(
            min: 0,
            max: _duration,
            values: _range,
            onChanged: _sending ? null : _onRange,
          ),
        const SizedBox(height: 8),
        TextField(
          controller: _overlay,
          maxLength: 60,
          maxLines: 2,
          decoration: const InputDecoration(
            labelText: 'نص على الفيديو (اختياري)',
            hintText: 'مثال: خصم 20% 🔥',
          ),
          onChanged: (_) => setState(() {}),
        ),
        TextField(
          controller: _title,
          maxLength: 80,
          decoration: const InputDecoration(labelText: 'عنوان قصير'),
        ),
        TextField(
          controller: _caption,
          maxLength: 300,
          maxLines: 3,
          decoration: const InputDecoration(labelText: 'وصف الإعلان'),
        ),
        TextField(
          controller: _activity,
          decoration: const InputDecoration(
            labelText: 'النشاط',
            hintText: 'مثال: زجاج، مقاولات، سيارات',
          ),
        ),
        TextField(
          controller: _contact,
          keyboardType: TextInputType.phone,
          decoration: const InputDecoration(
            labelText: 'رقم التواصل',
            helperText: 'اتركه فاضي ويطلع رقم المؤسسة',
          ),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: _allowDownload,
          onChanged: (v) => setState(() => _allowDownload = v),
          title: const Text('السماح بالتنزيل'),
          subtitle: const Text('الفيديو اللي ينزل عليه شعار خدوم'),
        ),
        if (_pay) ...[
          const Divider(),
          Text(
            '💳 إعلان مدفوع: ${((_quota!['paid_price_sar'] as num?) ?? 29).toStringAsFixed(0)} ريال. حوّل المبلغ على حساب خدوم (الظاهر في صفحة الباقات) واكتب اسم المحوّل:',
          ),
          TextField(
            controller: _transfer,
            decoration: const InputDecoration(labelText: 'اسم المحوّل'),
          ),
        ],
        const SizedBox(height: 16),
        if (_sending) ...[
          LinearProgressIndicator(value: _progress < 1 ? _progress : null),
          const SizedBox(height: 6),
          Text(
            _progress < 1
                ? 'جاري رفع الفيديو… ${(_progress * 100).round()}%'
                : 'جاري تجهيز الفيديو…',
            textAlign: TextAlign.center,
          ),
        ] else
          FilledButton(
            onPressed: _send,
            child: const Padding(
              padding: EdgeInsets.all(14),
              child: Text('إرسال للمراجعة'),
            ),
          ),
        TextButton(
          onPressed: _sending
              ? null
              : () async {
                  await _player?.dispose();
                  setState(() {
                    _player = null;
                    _file = null;
                  });
                },
          child: const Text('اختيار فيديو ثاني'),
        ),
      ],
    );
  }
}
