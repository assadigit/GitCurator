'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { verifyMagicToken } from '@/lib/api';

function AuthCallbackContent() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get('token');

  useEffect(() => {
    if (!token) {
      router.push('/login');
      return;
    }

    verifyMagicToken(token)
      .then(() => {
        router.push('/overview');
      })
      .catch(() => {
        router.push('/login?error=invalid_token');
      });
  }, [token, router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-muted-foreground">Authenticating...</p>
      </div>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center">
          <div className="text-center">
            <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="text-muted-foreground">Loading...</p>
          </div>
        </div>
      }
    >
      <AuthCallbackContent />
    </Suspense>
  );
}
