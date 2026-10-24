import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;

import '../branch_store.dart';

/// واجهة إعلانات الفيديو (صفحة «عروض»).
class VideoAdsApi {
  VideoAdsApi._(this.baseUri, this.token);

  final Uri baseUri;
  final String? token;

  static const _storage = FlutterSecureStorage();

  static Future<VideoAdsApi> create() async {
    final scope = await BranchPreferences.getInstance();
    final base =
        scope.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com';
    final token = await _storage.read(key: 'cloud_session_token');
    return VideoAdsApi._(Uri.parse(base), token);
  }

  Map<String, String> get authHeaders => {
    if (token != null && token!.isNotEmpty) 'Authorization': 'Bearer $token',
  };

  Uri resolve(String path) => baseUri.resolve(path);

  Future<dynamic> _json(String method, String path, [Object? body]) async {
    final request = http.Request(method, resolve(path))
      ..headers.addAll({...authHeaders, 'Content-Type': 'application/json'});
    if (body != null) request.body = jsonEncode(body);
    final streamed = await request.send().timeout(const Duration(seconds: 60));
    final text = await streamed.stream.bytesToString();
    final decoded = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
    if (streamed.statusCode < 200 || streamed.statusCode >= 300) {
      throw VideoAdsException(
        decoded is Map && decoded['error'] != null
            ? decoded['error'].toString()
            : 'تعذر الاتصال بالخادم',
      );
    }
    return decoded;
  }

  Future<List<Map<String, dynamic>>> feed({String activity = ''}) async {
    final query = activity.isEmpty
        ? ''
        : '?activity=${Uri.encodeQueryComponent(activity)}';
    final result = await _json('GET', '/api/video-ads/feed$query') as Map;
    return [
      for (final item in (result['items'] as List? ?? const []))
        Map<String, dynamic>.from(item as Map),
    ];
  }

  Future<Map<String, dynamic>> quota() async =>
      Map<String, dynamic>.from(await _json('GET', '/api/video-ads/quota') as Map);

  Future<Map<String, dynamic>> mine() async =>
      Map<String, dynamic>.from(await _json('GET', '/api/video-ads/mine') as Map);

  Future<int> createDraft(Map<String, dynamic> data) async {
    final result = await _json('POST', '/api/video-ads', data) as Map;
    return (result['id'] as num).toInt();
  }

  /// يرفع الفيديو كامل مع نقطة البداية والنهاية، والسيرفر يقصه.
  Future<void> upload(
    int id,
    File file, {
    required String contentType,
    double? start,
    double? end,
    void Function(double progress)? onProgress,
  }) => _uploadTo('/api/video-ads/$id/upload', file,
      contentType: contentType, start: start, end: end, onProgress: onProgress);

  Future<dynamic> _uploadTo(
    String path,
    File file, {
    required String contentType,
    double? start,
    double? end,
    void Function(double progress)? onProgress,
  }) async {
    final query = end == null
        ? ''
        : '?start=${(start ?? 0).toStringAsFixed(2)}&end=${end.toStringAsFixed(2)}';
    final length = await file.length();
    final request = http.StreamedRequest(
      'PUT',
      resolve('$path$query'),
    )
      ..headers.addAll({...authHeaders, 'Content-Type': contentType})
      ..contentLength = length;
    var sent = 0;
    final pipe = file.openRead().listen(
      (chunk) {
        sent += chunk.length;
        request.sink.add(chunk);
        onProgress?.call(length == 0 ? 1 : sent / length);
      },
      onDone: request.sink.close,
      onError: (Object error) {
        request.sink.addError(error);
        request.sink.close();
      },
      cancelOnError: true,
    );
    try {
      final response = await request.send().timeout(const Duration(minutes: 10));
      final text = await response.stream.bytesToString();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        final decoded = text.isEmpty ? null : jsonDecode(text);
        throw VideoAdsException(
          decoded is Map && decoded['error'] != null
              ? decoded['error'].toString()
              : 'تعذر رفع الفيديو',
        );
      }
      return text.isEmpty ? null : jsonDecode(text);
    } finally {
      await pipe.cancel();
    }
  }

  Future<Map<String, dynamic>> submit(
    int id, {
    bool pay = false,
    String transferName = '',
  }) async => Map<String, dynamic>.from(
    await _json('POST', '/api/video-ads/$id/submit', {
          if (pay) 'pay': true,
          if (pay) 'transferName': transferName,
        })
        as Map,
  );

  Future<void> delete(int id) => _json('DELETE', '/api/video-ads/$id');

  // ---------------- الأصوات (مثل سناب)
  Future<List<Map<String, dynamic>>> sounds({bool public = false}) async {
    final result = await _json('GET', '/api/video-ads/sounds${public ? '?scope=public' : ''}') as Map;
    return [
      for (final item in (result['items'] as List? ?? const []))
        Map<String, dynamic>.from(item as Map),
    ];
  }

  /// ينشئ صوت من جزء من فيديو في الجوال.
  Future<Map<String, dynamic>> createSound({
    required String name,
    required bool public,
    required File video,
    required double start,
    required double end,
    void Function(double progress)? onProgress,
  }) async {
    final draft = await _json('POST', '/api/video-ads/sounds', {'name': name, 'public': public}) as Map;
    final result = await _uploadTo(
      draft['uploadUrl'].toString(),
      video,
      contentType: contentTypeFor(video.path),
      start: start,
      end: end,
      onProgress: onProgress,
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> deleteSound(int id) => _json('DELETE', '/api/video-ads/sounds/$id');

  Future<Map<String, dynamic>> toggleLike(int id) async => Map<String, dynamic>.from(
    await _json('POST', '/api/video-ads/$id/like', {}) as Map,
  );

  Future<Uri> downloadLink(int id) async {
    final result = await _json('POST', '/api/video-ads/$id/download-link', {}) as Map;
    return resolve(result['url'].toString());
  }
}

class VideoAdsException implements Exception {
  VideoAdsException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// أرقام الجوال السعودية بصيغة واتساب الدولية.
String whatsappNumber(String raw) {
  var digits = raw.replaceAll(RegExp(r'[^0-9]'), '');
  if (digits.startsWith('00')) digits = digits.substring(2);
  if (digits.startsWith('05') && digits.length == 10) {
    digits = '966${digits.substring(1)}';
  } else if (digits.startsWith('5') && digits.length == 9) {
    digits = '966$digits';
  }
  return digits;
}

String contentTypeFor(String path) {
  final lower = path.toLowerCase();
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.webp')) return 'image/webp';
  if (lower.endsWith('.mov')) return 'video/quicktime';
  if (lower.endsWith('.webm')) return 'video/webm';
  if (lower.endsWith('.3gp')) return 'video/3gpp';
  return 'video/mp4';
}
