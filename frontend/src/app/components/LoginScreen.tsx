import { useState, useEffect } from 'react';
import { useNavigate, useLocation, Link } from 'react-router';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { login as loginApi } from '../api/auth';

export function LoginScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const auth = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const signupSuccess = (location.state as any)?.signupSuccess;

  useEffect(() => {
    if (auth.isAuthenticated) navigate('/workspace');
  }, [auth.isAuthenticated, navigate]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email || !password) {
      setError('Please enter both email and password');
      return;
    }

    setIsLoading(true);
    try {
      const response = await loginApi(email, password);
      auth.login(response);
      navigate('/workspace');
    } catch (err: any) {
      setError(err?.message || 'Login failed. Please check your credentials.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#fafafa] flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Logo/Brand Section */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-semibold text-[#111111] tracking-tight mb-2">
            Tri-State Enterprises
          </h1>
          <p className="text-sm text-[#737373]">
            HOA Budget Management System
          </p>
        </div>

        {/* Login Card */}
        <div className="bg-white border border-[#e5e5e5] rounded-lg shadow-sm p-8">
          <div className="mb-6">
            <h2 className="text-xl font-semibold text-[#111111] mb-1">Sign In</h2>
            <p className="text-sm text-[#737373]">
              Enter your credentials to access the system
            </p>
          </div>

          <form onSubmit={handleLogin} className="space-y-5">
            {/* Email Input */}
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-[#111111] mb-2">
                Email Address
              </label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="manager@tristate.com"
                className="h-11 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                disabled={isLoading}
              />
            </div>

            {/* Password Input */}
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-[#111111] mb-2">
                Password
              </label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  className="h-11 pr-11 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                  disabled={isLoading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded hover:bg-[#f5f5f5] transition-colors"
                  tabIndex={-1}
                >
                  {showPassword ? (
                    <EyeOff className="w-4 h-4 text-[#737373]" />
                  ) : (
                    <Eye className="w-4 h-4 text-[#737373]" />
                  )}
                </button>
              </div>
            </div>

            {/* Success Message (after signup) */}
            {signupSuccess && !error && (
              <div className="bg-[#d1fae5] border border-[#a7f3d0] text-[#065f46] px-4 py-3 rounded-lg text-sm">
                Account created successfully. Please sign in.
              </div>
            )}

            {/* Error Message */}
            {error && (
              <div className="bg-[#fee] border border-[#fcc] text-[#c33] px-4 py-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            {/* Login Button */}
            <Button
              type="submit"
              className="w-full h-11 bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
              disabled={isLoading}
            >
              {isLoading ? 'Signing In...' : 'Sign In'}
            </Button>
          </form>

          {/* Sign Up Link */}
          <div className="mt-6 pt-6 border-t border-[#e5e5e5]">
            <p className="text-sm text-center text-[#737373]">
              Don't have an account?{' '}
              <Link to="/signup" className="text-[#111111] hover:underline font-medium">
                Sign Up
              </Link>
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="mt-6 text-center text-xs text-[#a3a3a3]">
          © 2025 Tri-State Enterprises. All rights reserved.
        </div>
      </div>
    </div>
  );
}