import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as SonnerToaster } from "@/components/ui/sonner";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "Atenxion - Deterministic Document Extraction",
  description:
    "Parse documents, inspect parser evidence, and run schema-based deterministic data extraction.",
  keywords: [
    "document extraction",
    "document parsing",
    "deterministic extraction",
    "schema extraction",
    "Atenxion",
  ],
  authors: [{ name: "Atenxion" }],
  icons: {
    icon: "/atenxion_logo.png",
    apple: "/atenxion_logo.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className="antialiased bg-background text-foreground"
      >
        <Providers>{children}</Providers>
        <Toaster />
        <SonnerToaster position="top-right" richColors closeButton />
      </body>
    </html>
  );
}
