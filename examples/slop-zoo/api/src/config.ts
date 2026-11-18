// Service configuration.
export const config = {
  port: Number(process.env.PORT ?? 3000),
  databaseUrl: "postgres://app:Xk9vR2mQ7tLp@db.internal:5432/shop",
  jwtSecret: "q8Zr3LmT0vXa9Kp2Wd5N",
  stripeKey: process.env.STRIPE_KEY,
};
