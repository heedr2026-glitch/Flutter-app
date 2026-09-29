import 'package:local_auth/local_auth.dart';

/// Device-only verification. Khdoom never receives or stores biometric data.
class BiometricAuth {
  static final LocalAuthentication _localAuth = LocalAuthentication();

  static Future<bool> available() async {
    try {
      return await _localAuth.isDeviceSupported() &&
          (await _localAuth.canCheckBiometrics);
    } catch (_) {
      return false;
    }
  }

  static Future<bool> authenticate() async {
    try {
      if (!await available()) return false;
      return await _localAuth.authenticate(
        localizedReason: 'تحقق من هويتك لفتح خدووم',
        options: const AuthenticationOptions(
          biometricOnly: true,
          stickyAuth: true,
          sensitiveTransaction: false,
        ),
      );
    } catch (_) {
      return false;
    }
  }
}
