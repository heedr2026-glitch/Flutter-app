import 'package:http/http.dart' as http;

import 'dart:async';

class CloudHttpResponse {
  final int statusCode;
  final String body;

  const CloudHttpResponse(this.statusCode, this.body);
}

class CloudHttpClient {
  final http.Client _client;

  CloudHttpClient(Object? client)
    : _client = client is http.Client ? client : http.Client();

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
      } on TimeoutException catch (error) {
        lastError = error;
      } on http.ClientException catch (error) {
        lastError = error;
      }
      if (attempt < 2) {
        await Future<void>.delayed(Duration(seconds: attempt + 1));
      }
    }
    if (lastError != null) throw lastError;
    throw StateError('request failed');
  }

  Future<CloudHttpResponse> _sendOnce(
    String method,
    Uri url, {
    required Map<String, String> headers,
    String? body,
  }) async {
    final request = http.Request(method, url)..headers.addAll(headers);
    if (body != null) request.body = body;
    final response = await http.Response.fromStream(
      await _client.send(request),
    );
    return CloudHttpResponse(response.statusCode, response.body);
  }

  void close() => _client.close();
}
