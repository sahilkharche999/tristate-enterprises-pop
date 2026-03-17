import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  type AuthResponse,
  type User,
  logout as logoutApi,
  refreshToken as refreshTokenApi,
} from '../api/auth';
import { setTokenAccessor } from '../api/macros';

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

  const isAuthenticated = !!user && !!accessToken;

  // Keep macros.ts in sync with the current access token
  useEffect(() => {
    setTokenAccessor(() => accessToken);
  }, [accessToken]);

  const handleLogin = useCallback((response: AuthResponse) => {
    setUser(response.user);
    setAccessToken(response.access_token);
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await logoutApi();
    } catch {
      // Clear local state regardless
    }
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
  useEffect(() => {
    if (!accessToken) return;
    const timeout = setTimeout(
      async () => {
        try {
          const response = await refreshTokenApi();
          setUser(response.user);
          setAccessToken(response.access_token);
        } catch {
          setUser(null);
          setAccessToken(null);
        }
      },
      29 * 60 * 1000, // 29 minutes
    );
    return () => clearTimeout(timeout);
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
