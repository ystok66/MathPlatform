import type { Metadata } from "next";
import "katex/dist/katex.min.css";
import "./styles.css";
import { I18nProvider } from "./i18n/provider";

export const metadata: Metadata = {
  title: "AI-Assisted Elementary Math Olympiad Practice",
  description: "AI-assisted elementary math olympiad practice with a local question bank, reviewed question generation, targeted revision, and learning reports."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><I18nProvider>{children}</I18nProvider></body>
    </html>
  );
}
