import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:video_player/video_player.dart';

import '../khdoom_dark_page.dart';
import 'video_ads_api.dart';

/// يفتح مكتبة الأصوات (أصواتي / العامة) ويرجع الصوت المختار.
Future<Map<String, dynamic>?> pickSound(
  BuildContext context,
  VideoAdsApi api, {
  required int maxSeconds,
}) => Navigator.push<Map<String, dynamic>>(
  context,
  MaterialPageRoute(builder: (_) => SoundLibraryPage(api: api, maxSeconds: maxSeconds)),
);

class SoundLibraryPage extends StatefulWidget {
  const SoundLibraryPage({super.key, required this.api, required this.maxSeconds});
  final VideoAdsApi api;
  final int maxSeconds;

  @override
  State<SoundLibraryPage> createState() => _SoundLibraryPageState();
}

class _SoundLibraryPageState extends State<SoundLibraryPage> {
  List<Map<String, dynamic>> _mine = [];
  List<Map<String, dynamic>> _public = [];
  bool _loading = true;
  String? _error;
  VideoPlayerController? _preview;
  int? _playingId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _preview?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([widget.api.sounds(), widget.api.sounds(public: true)]);
      if (!mounted) return;
      setState(() {
        _mine = results[0];
        _public = results[1];
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

  Future<void> _play(Map<String, dynamic> sound) async {
    final id = (sound['id'] as num).toInt();
    final old = _preview;
    if (_playingId == id) {
      await old?.pause();
      setState(() => _playingId = null);
      return;
    }
    final player = VideoPlayerController.networkUrl(
      widget.api.resolve(sound['url'].toString()),
      httpHeaders: widget.api.authHeaders,
    );
    setState(() {
      _preview = player;
      _playingId = id;
    });
    await old?.dispose();
    try {
      await player.initialize();
      await player.play();
    } catch (_) {
      if (mounted) setState(() => _playingId = null);
    }
  }

  Future<void> _create() async {
    final made = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => CreateSoundPage(api: widget.api, maxSeconds: widget.maxSeconds),
      ),
    );
    if (made != null && mounted) Navigator.pop(context, made);
  }

  Future<void> _delete(Map<String, dynamic> sound) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => KhdoomDarkPage(
        child: Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            title: const Text('حذف الصوت'),
            content: Text('حذف «${sound['name']}»؟ الإعلانات اللي استخدمته تبقى زي ما هي.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('إلغاء')),
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: Colors.red),
                onPressed: () => Navigator.pop(context, true),
                child: const Text('حذف'),
              ),
            ],
          ),
        ),
      ),
    );
    if (ok != true) return;
    try {
      await widget.api.deleteSound((sound['id'] as num).toInt());
      _load();
    } catch (error) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.toString())));
    }
  }

  @override
  Widget build(BuildContext context) {
    return KhdoomDarkPage(
      child: Directionality(
        textDirection: TextDirection.rtl,
        child: DefaultTabController(
          length: 2,
          child: Scaffold(
            appBar: AppBar(
              title: const Text('🎵 الأصوات'),
              bottom: const TabBar(tabs: [Tab(text: 'أصواتي'), Tab(text: 'الأصوات العامة')]),
            ),
            floatingActionButton: FloatingActionButton.extended(
              onPressed: _create,
              icon: const Icon(Icons.add),
              label: const Text('أنشئ صوت من مقطع'),
            ),
            body: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(_error!, textAlign: TextAlign.center),
                    ),
                  )
                : TabBarView(
                    children: [
                      _list(_mine, mine: true),
                      _list(_public, mine: false),
                    ],
                  ),
          ),
        ),
      ),
    );
  }

  Widget _list(List<Map<String, dynamic>> items, {required bool mine}) {
    if (items.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            mine
                ? 'ما عندك أصوات بعد. اضغط «أنشئ صوت من مقطع» وخذ الصوت من أي فيديو في جوالك.'
                : 'ما فيه أصوات عامة بعد.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 90),
        children: [for (final sound in items) _card(sound, mine: mine)],
      ),
    );
  }

  Widget _card(Map<String, dynamic> sound, {required bool mine}) {
    final playing = _playingId == (sound['id'] as num).toInt();
    final uses = (sound['uses'] as num?)?.toInt() ?? 0;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            IconButton.filledTonal(
              iconSize: 30,
              onPressed: () => _play(sound),
              icon: Icon(playing ? Icons.pause : Icons.play_arrow),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    sound['organizationName']?.toString() ?? '',
                    style: const TextStyle(fontSize: 12, color: Colors.white70),
                  ),
                  Text(
                    '🎵 ${sound['name']}',
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
                  ),
                  Text(
                    '▶️ $uses استخدام${mine ? (sound['public'] == true ? ' · عام' : ' · خاص') : ''}',
                    style: const TextStyle(fontSize: 12, color: Colors.white70),
                  ),
                ],
              ),
            ),
            if (mine)
              IconButton(
                tooltip: 'حذف',
                onPressed: () => _delete(sound),
                icon: const Icon(Icons.delete_outline, color: Colors.redAccent),
              ),
            FilledButton(
              onPressed: () => Navigator.pop(context, sound),
              child: const Text('استخدم'),
            ),
          ],
        ),
      ),
    );
  }
}

/// «أنشئ صوت»: يختار فيديو، يحدد الجزء، يسميه، عام أو خاص.
class CreateSoundPage extends StatefulWidget {
  const CreateSoundPage({super.key, required this.api, required this.maxSeconds});
  final VideoAdsApi api;
  final int maxSeconds;

  @override
  State<CreateSoundPage> createState() => _CreateSoundPageState();
}

class _CreateSoundPageState extends State<CreateSoundPage> {
  File? _file;
  VideoPlayerController? _player;
  double _duration = 0;
  RangeValues _range = const RangeValues(0, 0);
  final _name = TextEditingController();
  bool _public = false;
  bool _sending = false;
  double _progress = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _pick());
  }

  @override
  void dispose() {
    _player?.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final picked = await ImagePicker().pickVideo(source: ImageSource.gallery);
    if (picked == null) {
      if (_file == null && mounted) Navigator.pop(context);
      return;
    }
    final file = File(picked.path);
    final player = VideoPlayerController.file(file);
    try {
      await player.initialize();
    } catch (_) {
      await player.dispose();
      _message('تعذر فتح المقطع، جرّب مقطع ثاني');
      return;
    }
    player.addListener(_loop);
    await _player?.dispose();
    final seconds = player.value.duration.inMilliseconds / 1000;
    if (!mounted) return;
    setState(() {
      _file = file;
      _player = player;
      _duration = seconds;
      _range = RangeValues(0, seconds.clamp(0, widget.maxSeconds.toDouble()));
    });
  }

  void _loop() {
    final player = _player;
    if (player == null || !player.value.isPlaying) {
      if (mounted) setState(() {});
      return;
    }
    final position = player.value.position.inMilliseconds / 1000;
    if (position >= _range.end) {
      player.seekTo(Duration(milliseconds: (_range.start * 1000).round()));
    }
  }

  void _onRange(RangeValues values) {
    var start = values.start;
    var end = values.end;
    if (end - start > widget.maxSeconds) {
      if (start != _range.start) {
        end = start + widget.maxSeconds;
      } else {
        start = end - widget.maxSeconds;
      }
    }
    if (end - start < 1) return;
    setState(() => _range = RangeValues(start, end));
    _player?.seekTo(Duration(milliseconds: (start * 1000).round()));
  }

  void _togglePlay() {
    final player = _player;
    if (player == null) return;
    if (player.value.isPlaying) {
      player.pause();
    } else {
      player.seekTo(Duration(milliseconds: (_range.start * 1000).round()));
      player.play();
    }
    setState(() {});
  }

  Future<void> _save() async {
    final file = _file;
    if (file == null) return;
    if (_name.text.trim().isEmpty) {
      _message('اكتب اسم للصوت');
      return;
    }
    await _player?.pause();
    setState(() {
      _sending = true;
      _progress = 0;
    });
    try {
      final sound = await widget.api.createSound(
        name: _name.text.trim(),
        public: _public,
        video: file,
        start: _range.start,
        end: _range.end,
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
      );
      if (!mounted) return;
      Navigator.pop(context, sound);
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
    final player = _player;
    return KhdoomDarkPage(
      child: Directionality(
        textDirection: TextDirection.rtl,
        child: Scaffold(
          appBar: AppBar(title: const Text('🎬 أنشئ صوت من مقطع')),
          body: player == null
              ? Center(
                  child: FilledButton.icon(
                    onPressed: _pick,
                    icon: const Icon(Icons.video_library),
                    label: const Text('اختر مقطع من جوالك'),
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    GestureDetector(
                      onTap: _togglePlay,
                      child: Container(
                        height: 220,
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
                            if (!player.value.isPlaying)
                              const Center(
                                child: Icon(Icons.play_circle_fill, size: 64, color: Colors.white70),
                              ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      '🎵 الجزء: ${_time(_range.start)} ← ${_time(_range.end)}  (${(_range.end - _range.start).round()} ثانية من ${widget.maxSeconds})',
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    if (_duration > 1)
                      RangeSlider(
                        min: 0,
                        max: _duration,
                        values: _range,
                        onChanged: _sending ? null : _onRange,
                      ),
                    TextField(
                      controller: _name,
                      maxLength: 60,
                      decoration: const InputDecoration(
                        labelText: 'اسم الصوت',
                        hintText: 'مثال: أمطار غزيرة',
                      ),
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: _public,
                      onChanged: _sending ? null : (v) => setState(() => _public = v),
                      title: Text(_public ? '🌍 عام' : '🔒 خاص'),
                      subtitle: Text(
                        _public
                            ? 'يطلع لكل المشتركين، عليه اسم مؤسستك وعدد الاستخدام'
                            : 'تستخدمه أنت بس',
                      ),
                    ),
                    const SizedBox(height: 12),
                    if (_sending) ...[
                      LinearProgressIndicator(value: _progress < 1 ? _progress : null),
                      const SizedBox(height: 6),
                      Text(
                        _progress < 1 ? 'جاري رفع المقطع… ${(_progress * 100).round()}%' : 'جاري تجهيز الصوت…',
                        textAlign: TextAlign.center,
                      ),
                    ] else
                      FilledButton(
                        onPressed: _save,
                        child: const Padding(
                          padding: EdgeInsets.all(14),
                          child: Text('حفظ الصوت'),
                        ),
                      ),
                    TextButton(
                      onPressed: _sending ? null : _pick,
                      child: const Text('اختيار مقطع ثاني'),
                    ),
                  ],
                ),
        ),
      ),
    );
  }
}
