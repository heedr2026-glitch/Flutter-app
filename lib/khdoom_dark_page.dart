import 'package:flutter/material.dart';

/// Scoped to feature pages so unrelated screens retain their existing styling.
class KhdoomDarkPage extends StatelessWidget {
  final Widget child;
  const KhdoomDarkPage({super.key, required this.child});
  @override
  Widget build(BuildContext context) => Theme(
    data: ThemeData(
      useMaterial3: true,
      fontFamily: 'sans-serif',

      brightness: Brightness.dark,
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xFF38BDF8),
        brightness: Brightness.dark,
      ).copyWith(surface: const Color(0xFF111B35), onSurface: Colors.white),
      scaffoldBackgroundColor: const Color(0xFF0B1020),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFF111B35),
        foregroundColor: Colors.white,
        surfaceTintColor: Colors.transparent,
      ),
      cardTheme: const CardThemeData(
        color: Color(0xFF172554),
        surfaceTintColor: Colors.transparent,
      ),
      dialogTheme: const DialogThemeData(backgroundColor: Color(0xFF111B35)),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xFF0B1020),
        labelStyle: const TextStyle(color: Colors.white70),
        hintStyle: const TextStyle(color: Colors.white54),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
    child: child,
  );
}
