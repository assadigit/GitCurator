import type { Metadata } from 'next';
import './globals.css';
import { ThemeProvider as NextThemesProvider } from 'next-themes';
import { Sidebar } from '@/components/sidebar';
import { QueryProvider } from '@/components/query-provider';

export const metadata: Metadata = {
  title: 'Curator Dashboard',
  description: 'GitHub Project Curator Bot — monitoring dashboard',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <NextThemesProvider attribute="class" defaultTheme="system" enableSystem>
          <QueryProvider>
            <div className="flex min-h-screen">
              <Sidebar />
              <main className="flex-1 p-8 overflow-auto">
                {children}
              </main>
            </div>
          </QueryProvider>
        </NextThemesProvider>
      </body>
    </html>
  );
}
