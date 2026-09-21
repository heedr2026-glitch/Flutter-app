import 'dart:convert';
import 'dart:async';
import 'dart:io';

class CloudHttpResponse {
  final int statusCode;
  final String body;

  const CloudHttpResponse(this.statusCode, this.body);
}

class CloudHttpClient {
  final HttpClient _client;

  CloudHttpClient(Object? client)
    : _client = client is HttpClient ? client : HttpClient() {
    _client.connectionTimeout = const Duration(seconds: 45);
    _client.idleTimeout = const Duration(seconds: 45);
  }

  Future<CloudHttpResponse> send(
    String method,
    Uri url, {
    required Map<String, String> headers,
    String? body,
  }) async {
    Object? lastError;
    for (var attempt = 0; attempt < (method == 'GET' ? 3 : 1); attempt++) {
      try {
        return await _sendOnce(
          method,
          url,
          headers: headers,
          body: body,
        ).timeout(const Duration(seconds: 30));
      } on SocketException catch (error) {
        lastError = error;
      } on TimeoutException catch (error) {
        lastError = error;
      }
      if (attempt < 2) {
        await Future<void>.delayed(Duration(seconds: attempt + 1));
      }
    }
    if (lastError != null) throw lastError;
    throw const SocketException('request failed');
  }

  Future<CloudHttpResponse> _sendOnce(
    String method,
    Uri url, {
    required Map<String, String> headers,
    String? body,
  }) async {
    final request = await _client.openUrl(method, url);
    request.headers.contentType = ContentType.json;
    headers.forEach(request.headers.set);
    if (body != null) {
      final encodedBody = utf8.encode(body);
      request.contentLength = encodedBody.length;
      request.add(encodedBody);
    }
    final response = await request.close();
    final bytes = await response.fold<List<int>>(
      <int>[],
      (buffer, chunk) => buffer..addAll(chunk),
    );
    return CloudHttpResponse(
      response.statusCode,
      utf8.decode(bytes, allowMalformed: true),
    );
  }

  void close() => _client.close(force: true);
}
