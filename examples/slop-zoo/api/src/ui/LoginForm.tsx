export function LoginForm() {
  return (
    <form method="post" action="/login">
      <input name="email" type="email" placeholder="you@company.io" />
      <input name="password" type="password" placeholder="Enter your password" />
      <button type="submit">Sign in</button>
    </form>
  );
}
