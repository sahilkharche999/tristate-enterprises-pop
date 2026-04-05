import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  type AuthResponse,
  type User,
  logout as logoutApi,
  refreshToken as refreshTokenApi,
} from '../api/auth';
import { setTokenAccessor, setTokenUpdater } from '../api/http';

interface AuthContextType {
  user: User | null;
  accessToken: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (response: AuthResponse) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const accessTokenRef = useRef<string | null>(null);

  const isAuthenticated = !!user && !!accessToken;

  useEffect(() => {
    setTokenAccessor(() => accessTokenRef.current);
    setTokenUpdater((token: string) => {
      accessTokenRef.current = token;
      setAccessToken(token);
    });
  }, []);

  const handleLogin = useCallback((response: AuthResponse) => {
    accessTokenRef.current = response.access_token;
    setUser(response.user);
    setAccessToken(response.access_token);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await logoutApi();
    } catch {
      // Clear local state regardless
    }
    accessTokenRef.current = null;
    setUser(null);
    setAccessToken(null);
  }, []);

  // On mount: try to restore session from httpOnly refresh cookie
  useEffect(() => {
    let cancelled = false;
    async function tryRefresh() {
      try {
        const response = await refreshTokenApi();
        if (!cancelled) {
          accessTokenRef.current = response.access_token;
          setUser(response.user);
          setAccessToken(response.access_token);
        }
      } catch {
        // No valid refresh cookie — user must log in
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    tryRefresh();
    return () => {
      cancelled = true;
    };
  }, []);

  // Silent refresh: 1 minute before the 30-min access token expires
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!accessToken) return;

    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);

    refreshTimerRef.current = setTimeout(async () => {
      // Retry up to 3 times with 2s gaps before giving up
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const response = await refreshTokenApi();
          accessTokenRef.current = response.access_token;
          setUser(response.user);
          setAccessToken(response.access_token);
          return; // success — new timer will be set by the re-render
        } catch {
          if (attempt < 2) {
            await new Promise((r) => setTimeout(r, 2000));
          }
        }
      }
      // All retries failed — log out
      accessTokenRef.current = null;
      setUser(null);
      setAccessToken(null);
    }, 29 * 60 * 1000);

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [accessToken]);

  const value = useMemo<AuthContextType>(
    () => ({
      user,
      accessToken,
      isLoading,
      isAuthenticated,
      login: handleLogin,
      logout: handleLogout,
    }),
    [user, accessToken, isLoading, isAuthenticated, handleLogin, handleLogout],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
