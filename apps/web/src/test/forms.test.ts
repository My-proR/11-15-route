import { describe, expect, it } from "vitest";
import { buildSchema, toDefaults, toPayload, type FieldDef } from "../components/ResourcePage";

const fields: FieldDef[] = [
  { name: "name", label: "Name", type: "text", required: true },
  { name: "work_email", label: "Email", type: "email", required: true },
  { name: "capacity", label: "Seats", type: "number", min: 1, max: 60 },
  { name: "lat", label: "Lat", type: "number", min: -90, max: 90, pair: "lon" },
  { name: "lon", label: "Lon", type: "number", min: -180, max: 180 },
  { name: "needs", label: "Needs", type: "tags" },
  { name: "is_active", label: "Active", type: "checkbox" },
];
const base = { name: "A", work_email: "a@b.co", capacity: "4", lat: "", lon: "", needs: "", is_active: true };

describe("resource form validation", () => {
  const schema = buildSchema(fields);
  it("accepts a valid record", () => expect(schema.safeParse(base).success).toBe(true));
  it("requires required fields", () => expect(schema.safeParse({ ...base, name: "" }).success).toBe(false));
  it("validates email", () => expect(schema.safeParse({ ...base, work_email: "nope" }).success).toBe(false));
  it("enforces numeric bounds", () => {
    expect(schema.safeParse({ ...base, capacity: "0" }).success).toBe(false);
    expect(schema.safeParse({ ...base, capacity: "61" }).success).toBe(false);
    expect(schema.safeParse({ ...base, capacity: "abc" }).success).toBe(false);
  });
  it("requires latitude and longitude together", () => {
    expect(schema.safeParse({ ...base, lat: "13" }).success).toBe(false);
    expect(schema.safeParse({ ...base, lat: "13", lon: "80" }).success).toBe(true);
    expect(schema.safeParse({ ...base, lat: "95", lon: "80" }).success).toBe(false);
  });
});

describe("payload mapping", () => {
  it("omits empty optionals on create and nulls them on update", () => {
    const v = { ...base, capacity: "", needs: "Wheelchair, ramp" };
    expect(toPayload(fields, v, "create")).toEqual({ name: "A", work_email: "a@b.co", needs: ["wheelchair", "ramp"], is_active: true });
    expect(toPayload(fields, v, "update")).toMatchObject({ capacity: null, lat: null, lon: null });
  });
  it("converts numbers and builds defaults from a row", () => {
    expect(toPayload(fields, base, "create").capacity).toBe(4);
    expect(toDefaults(fields, { id: "1", name: "Z", capacity: 3, needs: ["a", "b"], is_active: false })).toMatchObject({ name: "Z", capacity: "3", needs: "a, b", is_active: false });
    expect(toDefaults(fields).is_active).toBe(true);
  });
});
