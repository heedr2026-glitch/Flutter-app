import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:untitled1/branch_store.dart';
import 'package:untitled1/whatsapp_workspace.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    SharedPreferences.setMockInitialValues({
      'cloud_api_url': 'https://account.example',
      'admin_login_username': 'alice',
    });
    FlutterSecureStorage.setMockInitialValues({
      'cloud_session_token': 'private',
    });
  });
  test('session is never sent to a different backend origin', () async {
    final prefs = await BranchPreferences.getInstance();
    final api = WhatsAppGateway('https://other.example');
    await api.restore(prefs);
    expect(api.token, isNull);
    final same = WhatsAppGateway('https://account.example');
    await same.restore(prefs);
    expect(same.token, 'private');
  });
  test('employee does not inherit an admin WhatsApp session', () async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setString('session_user_type', 'employee');
    FlutterSecureStorage.setMockInitialValues({
      'cloud_session_token': 'private',
      'whatsapp_session_https://other.example_alice': 'admin',
    });
    final api = WhatsAppGateway('https://other.example');
    await api.restore(prefs);
    expect(api.token, isNull);
  });
  testWidgets('messages remain visible when Meta and later refresh fail', (
    tester,
  ) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setString('whatsapp_backend_url', 'https://account.example');
    final api = FakeWhatsAppGateway();
    await tester.pumpWidget(
      MaterialApp(
        home: WhatsAppWorkspace(inbox: true, gatewayFactory: (_) => api),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('hello'), findsOneWidget);
    expect(find.text('966500000000'), findsOneWidget);
    api.failMessages = true;
    await tester.pump(const Duration(seconds: 11));
    await tester.pumpAndSettle();
    expect(find.text('hello'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('conversation only shows the selected peer and has a composer', (
    tester,
  ) async {
    final prefs = await BranchPreferences.getInstance();
    await prefs.setString('whatsapp_backend_url', 'https://account.example');
    await tester.pumpWidget(
      MaterialApp(
        home: WhatsAppWorkspace(
          inbox: true,
          peer: '966500000000',
          gatewayFactory: (_) => FakeWhatsAppGateway(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('hello'), findsOneWidget);
    expect(find.text('other peer'), findsNothing);
    await tester.enterText(find.byType(TextField).last, 'reply');
    await tester.pump();
    expect(find.byIcon(Icons.send), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets(
    'empty setup never claims connection and retains editable fields',
    (tester) async {
      await tester.pumpWidget(const MaterialApp(home: WhatsAppWorkspace()));
      await tester.pumpAndSettle();
      expect(find.text('لم يتم فحص الاتصال بعد'), findsOneWidget);
      expect(find.text('ربط واتساب المؤسسة'), findsOneWidget);
      expect(find.text('حفظ وفحص الربط'), findsOneWidget);
      expect(find.byType(TextField), findsOneWidget);
    },
  );
}

class FakeWhatsAppGateway extends WhatsAppGateway {
  FakeWhatsAppGateway() : super('https://account.example');
  bool failMessages = false;
  @override
  Future<void> restore(BranchPreferences prefs) async {
    token = 'fake';
  }

  @override
  Future<dynamic> call(String path, {Map<String, dynamic>? data}) async {
    if (path.endsWith('/status')) throw Exception('Meta unavailable');
    if (failMessages) throw Exception('Messages unavailable');
    return {
      'messages': [
        {
          'id': '1',
          'peer': '966500000000',
          'body': 'hello',
          'direction': 'inbound',
          'state': 'received',
        },
        {
          'id': '2',
          'peer': '966511111111',
          'body': 'other peer',
          'direction': 'inbound',
          'state': 'received',
        },
      ],
    };
  }
}
