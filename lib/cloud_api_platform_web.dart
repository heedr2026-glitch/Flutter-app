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
    try {
      return await _sendOnce(
        method,
        url,
        headers: headers,
        body: body,
      ).timeout(const Duration(seconds: 60));
    } on TimeoutException {
      if (method != 'GET') rethrow;
      await Future<void>.delayed(const Duration(seconds: 2));
      return _sendOnce(
        method,
        url,
        headers: headers,
        body: body,
      ).timeout(const Duration(seconds: 60));
    }
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
