import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

class CommunityPage extends StatefulWidget {
  const CommunityPage({super.key});

  @override
  State<CommunityPage> createState() => _CommunityPageState();
}

class _CommunityPageState extends State<CommunityPage> {
  static const _storage = FlutterSecureStorage();
  final _composer = TextEditingController();
  KhdoomCloudApi? _api;
  List<dynamic> _posts = [];
  bool _loading = true;
  bool _sending = false;
  bool _refreshing = false;
  String? _error;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => _refreshSilently(),
    );
  }

  Future<KhdoomCloudApi> _client() async {
    final scope = await BranchPreferences.getInstance();
    final api = KhdoomCloudApi(
      scope: scope,
      baseUrl:
          scope.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    api.token = await _storage.read(key: 'cloud_session_token');
    return api;
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    await _fetchPosts(showErrors: true);
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _refreshSilently() async {
    if (!mounted || _refreshing || _sending) return;
    _refreshing = true;
    await _fetchPosts(showErrors: false);
    _refreshing = false;
  }

  Future<void> _fetchPosts({required bool showErrors}) async {
    try {
      _api ??= await _client();
      final posts = await _api!.communityPosts();
      if (mounted) {
        setState(() {
          _posts = posts;
          _error = null;
        });
      }
    } catch (error) {
      if (showErrors && mounted) {
        setState(
          () => _error = error.toString().replaceFirst('Exception: ', ''),
        );
      }
    }
  }

  Future<void> _publish() async {
    final body = _composer.text.trim();
    if (body.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      _api ??= await _client();
      await _api!.createCommunityPost(body);
      _composer.clear();
      await _load();
    } catch (error) {
      if (mounted) {
        setState(
          () => _error = error.toString().replaceFirst('Exception: ', ''),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  Future<void> _like(Map<String, dynamic> post) async {
    try {
      _api ??= await _client();
      final result = await _api!.likeCommunityPost(post['id']);
      if (!mounted) return;
      final id = post['id']?.toString();
      setState(() {
        for (final item in _posts) {
          if (item is Map && item['id']?.toString() == id) {
            item['like_count'] = result['likeCount'] ?? item['like_count'];
            item['liked_by_me'] = result['liked'] ?? item['liked_by_me'];
            break;
          }
        }
      });
    } catch (error) {
      if (mounted) {
        setState(
          () => _error = error.toString().replaceFirst('Exception: ', ''),
        );
      }
    }
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _composer.dispose();
    _api?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: const Text('مجتمع خدوم'),
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          actions: [
            IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
          ],
        ),
        body: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(12),
                child: Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF172554),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(
                      color: const Color(0xFF38BDF8).withValues(alpha: .35),
                    ),
                  ),
                  child: Column(
                    children: [
                      TextField(
                        controller: _composer,
                        maxLines: 3,
                        style: const TextStyle(color: Colors.white),
                        decoration: const InputDecoration(
                          hintText: 'شارك فكرة أو سؤالًا مع مؤسسات خدوم',
                          hintStyle: TextStyle(color: Colors.white60),
                          border: InputBorder.none,
                        ),
                      ),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: FilledButton.icon(
                          onPressed: _sending ? null : _publish,
                          icon: _sending
                              ? const SizedBox.square(
                                  dimension: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Icon(Icons.send),
                          label: const Text('نشر'),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  child: Text(
                    _error!,
                    style: const TextStyle(color: Colors.redAccent),
                  ),
                ),
              Expanded(
                child: _loading
                    ? const Center(child: CircularProgressIndicator())
                    : _posts.isEmpty
                    ? const Center(
                        child: Text(
                          'لا توجد منشورات بعد',
                          style: TextStyle(color: Colors.white70),
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: _load,
                        child: ListView.builder(
                          padding: const EdgeInsets.fromLTRB(12, 0, 12, 20),
                          itemCount: _posts.length,
                          itemBuilder: (_, index) {
                            final post = Map<String, dynamic>.from(
                              _posts[index] as Map,
                            );
                            final liked =
                                post['liked_by_me'] == true ||
                                post['liked_by_me'] == 1;
                            return Card(
                              color: const Color(0xFF111B35),
                              margin: const EdgeInsets.only(bottom: 10),
                              child: Padding(
                                padding: const EdgeInsets.all(14),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      post['organization_name'] == null
                                          ? (post['name']?.toString() ??
                                                'إدارة خدوم')
                                          : '${post['organization_name']} — ${post['user_name'] ?? 'مستخدم'}',
                                      style: const TextStyle(
                                        color: Color(0xFF7DD3FC),
                                        fontWeight: FontWeight.bold,
                                      ),
                                    ),
                                    const SizedBox(height: 8),
                                    Text(
                                      post['body']?.toString() ?? '',
                                      style: const TextStyle(
                                        color: Colors.white,
                                        fontSize: 16,
                                      ),
                                    ),
                                    const SizedBox(height: 8),
                                    Row(
                                      children: [
                                        IconButton(
                                          onPressed: () => _like(post),
                                          icon: Icon(
                                            liked
                                                ? Icons.favorite
                                                : Icons.favorite_border,
                                            color: liked
                                                ? Colors.redAccent
                                                : Colors.pinkAccent,
                                          ),
                                        ),
                                        Text(
                                          '${post['like_count'] ?? 0}',
                                          style: const TextStyle(
                                            color: Colors.white70,
                                          ),
                                        ),
                                      ],
                                    ),
                                  ],
                                ),
                              ),
                            );
                          },
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

class CommunityChatPage extends StatefulWidget {
  const CommunityChatPage({super.key});

  @override
  State<CommunityChatPage> createState() => _CommunityChatPageState();
}

class _CommunityChatPageState extends State<CommunityChatPage> {
  final _input = TextEditingController();
  KhdoomCloudApi? _api;
  List<dynamic> _messages = [];
  bool _loading = true;
  bool _sending = false;
  bool _refreshing = false;
  String? _error;
  Timer? _refreshTimer;

  Future<KhdoomCloudApi> _client() async {
    final scope = await BranchPreferences.getInstance();
    final api = KhdoomCloudApi(
      scope: scope,
      baseUrl:
          scope.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    );
    api.token = await _CommunityPageState._storage.read(
      key: 'cloud_session_token',
    );
    return api;
  }

  Future<void> _load() async {
    try {
      _api ??= await _client();
      final messages = await _api!.communityChat();
      if (mounted) {
        setState(() {
          _messages = messages;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _refreshSilently() async {
    if (!mounted || _refreshing || _sending) return;
    _refreshing = true;
    try {
      _api ??= await _client();
      final lastId = _messages.isEmpty
          ? 0
          : int.tryParse(_messages.last['id'].toString()) ?? 0;
      final messages = await _api!.communityChat(after: lastId);
      if (messages.isNotEmpty && mounted) {
        setState(() => _messages = [..._messages, ...messages]);
      }
    } finally {
      _refreshing = false;
    }
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      _api ??= await _client();
      await _api!.sendCommunityChat(text);
      _input.clear();
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _load();
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 4),
      (_) => _refreshSilently(),
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _input.dispose();
    _api?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          title: const Text('محادثة مباشرة مع خدوم'),
          foregroundColor: Colors.white,
          backgroundColor: const Color(0xFF111B35),
        ),
        body: Column(
          children: [
            if (_error != null)
              Padding(
                padding: const EdgeInsets.all(10),
                child: Text(
                  _error!,
                  style: const TextStyle(color: Colors.redAccent),
                ),
              ),
            Expanded(
              child: _loading
                  ? const Center(child: CircularProgressIndicator())
                  : ListView.builder(
                      padding: const EdgeInsets.all(12),
                      itemCount: _messages.length,
                      itemBuilder: (_, i) {
                        final m = Map<String, dynamic>.from(
                          _messages[i] as Map,
                        );
                        final admin = m['sender'] == 'admin';
                        return Align(
                          alignment: admin
                              ? Alignment.centerLeft
                              : Alignment.centerRight,
                          child: Container(
                            margin: const EdgeInsets.only(bottom: 8),
                            padding: const EdgeInsets.all(12),
                            decoration: BoxDecoration(
                              color: admin
                                  ? const Color(0xFF15345F)
                                  : const Color(0xFF087CAB),
                              borderRadius: BorderRadius.circular(14),
                            ),
                            child: Text(
                              m['body']?.toString() ?? '',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                              ),
                            ),
                          ),
                        );
                      },
                    ),
            ),
            Padding(
              padding: const EdgeInsets.all(10),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _input,
                      style: const TextStyle(color: Colors.white),
                      decoration: const InputDecoration(
                        hintText: 'اكتب رسالتك للإدارة',
                        hintStyle: TextStyle(color: Colors.white60),
                      ),
                    ),
                  ),
                  IconButton(
                    onPressed: _sending ? null : _send,
                    icon: const Icon(Icons.send, color: Color(0xFF38D4FF)),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
