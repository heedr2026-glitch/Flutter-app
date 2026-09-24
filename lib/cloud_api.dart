import 'dart:convert';

import 'cloud_api_platform_io.dart'
    if (dart.library.html) 'cloud_api_platform_web.dart';

import 'branch_store.dart';

class CloudApiException implements Exception {
  final int statusCode;
  final String message;

  const CloudApiException(this.statusCode, this.message);

  @override
  String toString() => message;
}

class KhdoomCloudApi {
  final Uri baseUri;
  final CloudHttpClient _client;
  String? token;
  final Future<BranchPreferences> _branchScope;

  KhdoomCloudApi({
    String baseUrl = 'https://khdoom-api.onrender.com',
    Object? client,
    BranchPreferences? scope,
  }) : baseUri = Uri.parse(baseUrl),
       _branchScope = scope == null
           ? BranchPreferences.getInstance()
           : Future.value(scope),
       _client = CloudHttpClient(client);

  Future<Map<String, dynamic>> register(Map<String, dynamic> data) async {
    final result = await _request('POST', '/api/register', body: data);
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> login(
    String username,
    String password, {
    String deviceId = '',
    String deviceName = 'جهاز غير معروف',
  }) async {
    final result = await _request(
      'POST',
      '/api/login',
      body: {
        'username': username,
        'password': password,
        'deviceId': deviceId,
        'deviceName': deviceName,
      },
    ) as Map<String, dynamic>;
    token = result['token'] as String?;
    return result;
  }

  Future<void> validateSession() async {
    await _request('GET', '/api/session-status');
  }

  Future<Map<String, dynamic>> maintenanceStatus() async {
    final result = await _request('GET', '/api/maintenance-status');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> auditLogs() async {
    final result = await _request('GET', '/api/audit-logs');
    return List<dynamic>.from(result as List);
  }

  Future<List<dynamic>> securitySessions() async {
    final result = await _request('GET', '/api/security/sessions');
    return List<dynamic>.from(result as List);
  }

  Future<void> setSessionTrusted(Object id, bool trusted) async {
    await _request(
      'PUT',
      '/api/security/sessions/$id',
      body: {'trusted': trusted},
    );
  }

  Future<void> disconnectSession(Object id) async {
    await _request('DELETE', '/api/security/sessions/$id');
  }

  Future<int> logoutAllSessions({bool keepCurrent = true}) async {
    final result = await _request(
      'POST',
      '/api/security/logout-all',
      body: {'keepCurrent': keepCurrent},
    ) as Map<String, dynamic>;
    return (result['disconnected'] as num?)?.toInt() ?? 0;
  }

  Future<Map<String, dynamic>> organization() async {
    final result = await _request('GET', '/api/organization');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> updateOrganization(Map<String, dynamic> data) async {
    await _request('PUT', '/api/organization', body: data);
  }

  Future<List<dynamic>> organizationAlertRecords() async {
    final result = await _request('GET', '/api/organization/alerts');
    final map = Map<String, dynamic>.from(result as Map);
    return List<dynamic>.from(map['records'] as List? ?? const []);
  }

  Future<void> updateOrganizationAlertRecords(List<dynamic> records) async {
    await _request(
      'PUT',
      '/api/organization/alerts',
      body: {'records': records},
    );
  }

  Future<List<dynamic>> employees({bool allBranches = false}) async {
    final scope = await _branchScope;
    final result = await _request('GET', '/api/employees');
    final items = List<dynamic>.from(result as List);
    if (allBranches) return items;
    return items
        .where(
          (item) =>
              BranchPreferences.employeeBranch(item as Map) == scope.branchId,
        )
        .toList();
  }

  Future<void> _requireEmployeeInBranch(Object id) async {
    final items = await employees();
    if (!items.any((item) => item['id'].toString() == id.toString())) {
      throw const CloudApiException(404, 'الموظف غير موجود في هذا الفرع');
    }
  }

  Future<List<dynamic>> cameras() async => List<dynamic>.from(
    await _request('GET', '/api/cameras') as List,
  );

  Future<Map<String, dynamic>> createCamera(Map<String, dynamic> camera) async =>
      Map<String, dynamic>.from(
        await _request('POST', '/api/cameras', body: camera) as Map,
      );

  Future<void> deleteCamera(Object id) async {
    await _request('DELETE', '/api/cameras/$id');
  }

  Future<Map<String, dynamic>> createEmployeeInvitation(
    Map<String, dynamic> invitation,
  ) async => Map<String, dynamic>.from(
    await _request('POST', '/api/employee-invitations', body: invitation) as Map,
  );

  Future<List<dynamic>> employeeInvitations() async => List<dynamic>.from(
    await _request('GET', '/api/employee-invitations') as List,
  );

  Future<Map<String, dynamic>> createEmployee(
    Map<String, dynamic> employee,
  ) async {
    final scope = await _branchScope;
    final result = await _request(
      'POST',
      '/api/employees',
      body: scope.tagEmployee(employee),
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> updateEmployee(
    Object id,
    Map<String, dynamic> employee,
  ) async {
    final scope = await _branchScope;
    await _requireEmployeeInBranch(id);
    final result = await _request(
      'PUT',
      '/api/employees/$id',
      body: scope.tagEmployee(employee),
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> deleteEmployee(Object id) async {
    await _requireEmployeeInBranch(id);
    await _request('DELETE', '/api/employees/$id');
  }

  Future<List<dynamic>> appointments() async {
    final branch = (await _branchScope).branchId;
    final result = await _request(
      'GET',
      '/api/appointments?branchId=${Uri.encodeQueryComponent(branch)}',
    );
    // Also isolate responses from older servers during rollout.
    return List<dynamic>.from(result as List)
        .where(
          (item) => (item['branch_id'] ?? BranchPreferences.mainId) == branch,
        )
        .toList();
  }

  Future<void> deleteAppointment(Object id) async {
    final branch = (await _branchScope).branchId;
    await _request(
      'DELETE',
      '/api/appointments/$id?branchId=${Uri.encodeQueryComponent(branch)}',
    );
  }

  Future<String> branchChatLink() async {
    final scope = await _branchScope;
    final result = await _request(
      'POST',
      '/api/branch-chat',
      body: {'branchId': scope.branchId, 'branchName': scope.branchName},
    ) as Map;
    return baseUri.resolve(result['path'] as String).toString();
  }

  Future<void> replyToAppointment(
    Object id,
    String message,
    int throughId,
  ) async {
    final branch = (await _branchScope).branchId;
    await _request(
      'POST',
      '/api/appointments/$id/followup-reply?branchId=${Uri.encodeQueryComponent(branch)}',
      body: {'message': message, 'throughMessageId': throughId},
    );
  }

  Future<Map<String, dynamic>> updateAppointmentStatus(
    Object id,
    String status,
  ) async {
    return updateAppointment(id, {'status': status});
  }

  Future<Map<String, dynamic>> updateAppointment(
    Object id,
    Map<String, dynamic> appointment,
  ) async {
    final branch = (await _branchScope).branchId;
    if (!(await appointments()).any(
      (item) => item['id'].toString() == id.toString(),
    )) {
      throw const CloudApiException(403, 'الطلب لا يتبع هذا الفرع');
    }
    final result = await _request(
      'PUT',
      '/api/appointments/$id?branchId=${Uri.encodeQueryComponent(branch)}',
      body: appointment,
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> vehicles() async {
    if ((await _branchScope).branchId != BranchPreferences.mainId) return [];
    final result = await _request('GET', '/api/vehicles');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> activateCode(String code) async {
    final result = await _request(
      'POST',
      '/api/activate-code',
      body: {'code': code.trim().toUpperCase()},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> activateCodeBeforeLogin(
    String username,
    String code,
  ) async {
    final result = await _request(
      'POST',
      '/api/activate-code-public',
      body: {
        'username': username.trim().toLowerCase(),
        'code': code.trim().toUpperCase(),
      },
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> previewDiscountCode(
    String code,
    String package, {
    required int durationMonths,
  }) async {
    final result = await _request(
      'POST',
      '/api/discount-code/preview',
      body: {'code': code.trim().toUpperCase(), 'package': package, 'durationMonths': durationMonths},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> requestSubscription({
    required String package,
    required String transferName,
    String transferReceipt = '',
    String discountCode = '',
    int? offerId,
    required int durationMonths,
  }) async {
    final result = await _request(
      'POST',
      '/api/subscription-requests',
      body: {
        'package': package,
        'transferName': transferName.trim(),
        'transferReceipt': transferReceipt,
        'discountCode': discountCode.trim().toUpperCase(),
        if (offerId != null) 'offerId': offerId,
        'durationMonths': durationMonths,
      },
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> packageOffers() async {
    final result = await _request('GET', '/api/package-offers');
    return List<dynamic>.from(result as List);
  }

  Future<List<dynamic>> packageCatalog() async {
    final result = await _request('GET', '/api/package-catalog');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> paymentSettings() async {
    final result = await _request('GET', '/api/payment-settings');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> packageLimits() async {
    final result = await _request('GET', '/api/package-limits');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> supportTickets() async {
    final result = await _request('GET', '/api/support-tickets');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> createSupportTicket({
    required String category,
    required String message,
  }) async {
    final result = await _request(
      'POST',
      '/api/support-tickets',
      body: {'category': category, 'message': message},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> deleteSupportTicket(int id) async {
    await _request('DELETE', '/api/support-tickets/$id');
  }

  Future<List<dynamic>> advertisements() async {
    final result = await _request('GET', '/api/ads');
    return List<dynamic>.from(result as List);
  }

  Future<List<dynamic>> myAdvertisements() async {
    final result = await _request('GET', '/api/my-ads');
    return List<dynamic>.from(result as List);
  }

  Future<void> deleteAdvertisement(int id) async {
    await _request('DELETE', '/api/ads/$id');
  }

  Future<Map<String, dynamic>> createAdvertisement({
    required String title,
    required String message,
    required String contact,
    int requestedDays = 10,
    String imageData = '',
  }) async {
    final result = await _request(
      'POST',
      '/api/ads',
      body: {
        'title': title,
        'message': message,
        'contact': contact,
        'requestedDays': requestedDays,
        'imageData': imageData,
      },
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> commercialReport(
    Map<String, dynamic> report,
  ) async {
    final result = await _request(
      'POST',
      '/api/ai/commercial-report',
      body: {'report': report},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> commercialResearch({
    required String keywords,
    required String city,
    required String researchType,
  }) async {
    final result = await _request(
      'POST',
      '/api/ai/commercial-research',
      body: {'keywords': keywords, 'city': city, 'researchType': researchType},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> receptionConversation() async {
    final branch = (await _branchScope).branchId;
    return Map<String, dynamic>.from(
      await _request(
        'GET',
        '/api/reception/conversation?branchId=${Uri.encodeQueryComponent(branch)}',
      ) as Map,
    );
  }

  Future<Map<String, dynamic>> sendReceptionMessage(
    String message,
    String clientMessageId, {
    Map<String, dynamic> settings = const {},
  }) async {
    final branch = (await _branchScope).branchId;
    return Map<String, dynamic>.from(
      await _request(
        'POST',
        '/api/reception/conversation',
        body: {
          'message': message,
          'clientMessageId': clientMessageId,
          'branchId': branch,
          'settings': settings,
        },
      ) as Map,
    );
  }

  Future<Map<String, dynamic>> receptionReply({
    required String message,
    List<Map<String, String>> history = const [],
    required Map<String, dynamic> settings,
  }) async {
    final result = await _request(
      'POST',
      '/api/ai/reception-reply',
      body: {
        'message': message,
        'settings': settings,
        'history': history.length > 12
            ? history.sublist(history.length - 12)
            : history,
      },
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> aiTraining() async {
    final result = await _request('GET', '/api/ai-training');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> aiProfile() async {
    final result = await _request('GET', '/api/ai-profile');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> saveAiProfile(Map<String, dynamic> profile) async {
    await _request('PUT', '/api/ai-profile', body: profile);
  }

  Future<List<dynamic>> aiTrainingMessages() async {
    final result = await _request('GET', '/api/ai-training/messages');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> aiTrainingChat(
    String employeeType,
    String message,
  ) async {
    final scope = await _branchScope;
    final result = await _request(
      'POST',
      '/api/ai-training/chat',
      body: {
        'employeeType': employeeType,
        'message': message,
        'context': {'branchName': scope.branchName},
      },
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> assistantConversation(
    String message,
    List<Map<String, String>> history,
    Map<String, dynamic> context,
  ) async {
    final result = await _request(
      'POST',
      '/api/ai/assistant',
      body: {'message': message, 'history': history, 'context': context},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<void> saveAiTraining(String employeeType, String content) async {
    await _request(
      'PUT',
      '/api/ai-training',
      body: {'employeeType': employeeType, 'content': content},
    );
  }

  Future<List<dynamic>> aiTrainingInstructions() async {
    final result = await _request('GET', '/api/ai-training/instructions');
    return List<dynamic>.from(result as List);
  }

  Future<void> createAiTrainingInstruction(
    String employeeType,
    String content, {
    String status = 'draft',
  }) async {
    await _request(
      'POST',
      '/api/ai-training/instructions',
      body: {
        'employeeType': employeeType,
        'content': content,
        'status': status,
      },
    );
  }

  Future<void> updateAiTrainingInstruction(
    int id,
    String content,
    String status,
  ) async {
    await _request(
      'PUT',
      '/api/ai-training/instructions/$id',
      body: {'content': content, 'status': status},
    );
  }

  Future<void> deleteAiTrainingInstruction(int id) async {
    await _request('DELETE', '/api/ai-training/instructions/$id');
  }

  Future<Map<String, dynamic>> subscription() async {
    final result = await _request('GET', '/api/subscription');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> branchQuote({String period = 'monthly'}) async =>
      Map<String, dynamic>.from(
        await _request('GET', '/api/addons/quote?period=$period') as Map,
      );

  Future<Map<String, dynamic>> createCloudBranch(
    Map<String, dynamic> data,
  ) async => Map<String, dynamic>.from(
    await _request('POST', '/api/addons/branches', body: data) as Map,
  );

  Future<List<dynamic>> registeredBranches() async =>
      List<dynamic>.from(await _request('GET', '/api/addons/branches') as List);

  Future<Map<String, dynamic>> requestAdditionalOrganization(
    Map<String, dynamic> data,
  ) async => Map<String, dynamic>.from(
    await _request('POST', '/api/addons/organizations', body: data) as Map,
  );

  Future<dynamic> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final headers = <String, String>{'Content-Type': 'application/json'};
    if (token case final activeToken?) {
      headers['Authorization'] = 'Bearer $activeToken';
      headers['X-Branch-Id'] = path.startsWith('/api/addons/')
          ? 'main'
          : (await _branchScope).branchId;
    }
    final watch = Stopwatch()..start();
    var performanceReported = false;
    try {
      final response = await _client.send(
        method,
        baseUri.resolve(path),
        headers: headers,
        body: body == null ? null : jsonEncode(body),
      );
      watch.stop();
      final text = response.body;
      final decoded = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
      final succeeded = response.statusCode >= 200 && response.statusCode < 300;
      if (!succeeded || watch.elapsedMilliseconds >= 1500) {
        _reportPerformance(
          path: path,
          operation: method,
          elapsedMs: watch.elapsedMilliseconds,
          success: succeeded,
          statusCode: response.statusCode,
        );
        performanceReported = true;
      }
      if (!succeeded) {
        final message = decoded is Map ? decoded['error']?.toString() : null;
        throw CloudApiException(
          response.statusCode,
          message ?? 'تعذر الاتصال بالخادم',
        );
      }
      return decoded;
    } catch (_) {
      if (watch.isRunning) watch.stop();
      // A network failure can still be reported when the API becomes reachable.
      // The report contains no request content, token, or personal data.
      if (!performanceReported && path != '/api/performance-events') {
        _reportPerformance(
          path: path,
          operation: method,
          elapsedMs: watch.elapsedMilliseconds,
          success: false,
          statusCode: 0,
        );
      }
      rethrow;
    }
  }

  Future<void> _reportPerformance({
    required String path,
    required String operation,
    required int elapsedMs,
    required bool success,
    required int statusCode,
  }) async {
    if (path == '/api/performance-events' || token == null || token!.isEmpty) {
      return;
    }
    try {
      await _client.send(
        'POST',
        baseUri.resolve('/api/performance-events'),
        headers: <String, String>{
          'Content-Type': 'application/json',
          'Authorization': 'Bearer $token',
        },
        body: jsonEncode(<String, dynamic>{
          'page': path.split('?').first,
          'operation': operation,
          'elapsedMs': elapsedMs.clamp(0, 120000),
          'success': success,
          'statusCode': statusCode.clamp(0, 599),
        }),
      );
    } catch (_) {
      // Monitoring must never affect the user's original request.
    }
  }

  Future<List<dynamic>> communityPosts({int page = 1}) async {
    final result = await _request('GET', '/api/community/posts?page=$page');
    final map = Map<String, dynamic>.from(result as Map);
    return List<dynamic>.from(map['items'] as List? ?? const []);
  }

  Future<Map<String, dynamic>> createCommunityPost(String body) async {
    final result = await _request(
      'POST',
      '/api/community/posts',
      body: {'body': body.trim()},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> communityChat({int after = 0}) async {
    final result = await _request('GET', '/api/community/chat?after=$after');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> sendCommunityChat(String body) async {
    return Map<String, dynamic>.from(
      await _request('POST', '/api/community/chat', body: {'body': body})
          as Map,
    );
  }

  Future<Map<String, dynamic>> callsConfig() async {
    final result = await _request('GET', '/api/calls/config');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> connectCalls() async {
    final result = await _request('POST', '/api/calls/connect', body: const {});
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> callLogs() async {
    final result = await _request('GET', '/api/calls');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> requestOutboundCall(String phone) async {
    final result = await _request(
      'POST',
      '/api/calls/outbound',
      body: {'phone': phone},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  Future<Map<String, dynamic>> likeCommunityPost(Object id) async {
    final result = await _request('POST', '/api/community/posts/$id/like');
    return Map<String, dynamic>.from(result as Map);
  }

  Future<List<dynamic>> communityComments(Object id) async {
    final result = await _request('GET', '/api/community/posts/$id/comments');
    return List<dynamic>.from(result as List);
  }

  Future<Map<String, dynamic>> addCommunityComment(
    Object id,
    String body,
  ) async {
    final result = await _request(
      'POST',
      '/api/community/posts/$id/comments',
      body: {'body': body.trim()},
    );
    return Map<String, dynamic>.from(result as Map);
  }

  void close() => _client.close();
}
