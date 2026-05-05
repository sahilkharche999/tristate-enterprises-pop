import { useState } from 'react';
import { useNavigate, Link } from 'react-router';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { Eye, EyeOff } from 'lucide-react';
import { signup as signupApi } from '../api/auth';

export function SignupScreen() {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!name || !email || !password || !confirmPassword) {
      setError('Please fill in all fields');
      return;
    }

    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setIsLoading(true);
    try {
      await signupApi(email, name, password);
      navigate('/', { state: { signupSuccess: true } });
    } catch (err: any) {
      if (err?.status === 409) {
        setError('An account with this email already exists');
      } else {
        setError(err?.message || 'Signup failed. Please try again.');
      }
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
          <p className="text-sm text-[#737373]">HOA Budget Management System</p>
        </div>

        {/* Signup Card */}
        <div className="bg-white border border-[#e5e5e5] rounded-lg shadow-sm p-8">
          <div className="mb-6">
            <h2 className="text-xl font-semibold text-[#111111] mb-1">Create Account</h2>
            <p className="text-sm text-[#737373]">Sign up to get started</p>
          </div>

          <form onSubmit={handleSignup} className="space-y-5">
            {/* Name Input */}
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-[#111111] mb-2">
                Full Name
              </label>
              <Input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="John Doe"
                className="h-11 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                disabled={isLoading}
              />
            </div>

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
                  placeholder="Minimum 8 characters"
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

            {/* Confirm Password Input */}
            <div>
              <label
                htmlFor="confirmPassword"
                className="block text-sm font-medium text-[#111111] mb-2"
              >
                Confirm Password
              </label>
              <Input
                id="confirmPassword"
                type={showPassword ? 'text' : 'password'}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter your password"
                className="h-11 bg-white border-[#e5e5e5] text-[#111111] placeholder:text-[#a3a3a3] focus:border-[#737373] focus:ring-1 focus:ring-[#737373]"
                disabled={isLoading}
              />
            </div>

            {/* Error Message */}
            {error && (
              <div className="bg-[#fee] border border-[#fcc] text-[#c33] px-4 py-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            {/* Signup Button */}
            <Button
              type="submit"
              className="w-full h-11 bg-[#111111] text-white hover:bg-[#262626] shadow-sm"
              disabled={isLoading}
            >
              {isLoading ? 'Creating Account...' : 'Create Account'}
            </Button>
          </form>

          {/* Sign In Link */}
          <div className="mt-6 pt-6 border-t border-[#e5e5e5]">
            <p className="text-sm text-center text-[#737373]">
              Already have an account?{' '}
              <Link to="/" className="text-[#111111] hover:underline font-medium">
                Sign In
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
