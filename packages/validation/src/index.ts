import { z } from "zod";

/** Mirrors services/api/app/schemas.py for the safety-critical payloads shared by web and mobile. */
export const latitude = z.number().min(-90).max(90);
export const longitude = z.number().min(-180).max(180);

export const sosLocation = z
  .object({ status: z.enum(["obtained", "denied", "unavailable"]), latitude: latitude.optional(), longitude: longitude.optional(), accuracy_m: z.number().min(0).max(100000).optional(), captured_at: z.string().datetime({ offset: true }).optional() })
  .refine((v) => v.status !== "obtained" || (v.latitude !== undefined && v.longitude !== undefined && v.captured_at !== undefined), { message: "status 'obtained' requires latitude, longitude and captured_at" });

export const sosCreate = z.object({ message: z.string().max(500).default(""), allocation_id: z.string().uuid().optional(), location: sosLocation.optional() });

export const positionIn = z.object({
  allocation_id: z.string().uuid(), latitude, longitude, accuracy_m: z.number().min(0).max(100000).optional(), speed_kmh: z.number().min(0).max(300).optional(),
  heading: z.number().min(0).max(360).optional(), captured_at: z.string().datetime({ offset: true }),
});

export const leaveIn = z.object({ driver_id: z.string().uuid().optional(), starts_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), ends_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/), reason: z.string().max(300).default("") })
  .refine((v) => v.ends_on >= v.starts_on, { message: "ends_on must be on or after starts_on", path: ["ends_on"] });

export const registration = z.string().transform((v) => v.replace(/\s+/g, "").toUpperCase()).pipe(z.string().regex(/^[A-Z0-9-]{3,20}$/, "registration_number must be alphanumeric"));
