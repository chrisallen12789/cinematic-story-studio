const sensitiveName =
  /(?:^|_)(?:API_KEY|ACCESS_KEY|SECRET_KEY|CLIENT_SECRET|PASSWORD|TOKEN|SECRET)$/i;

export function withoutSensitiveValues(source = process.env) {
  const environment = { ...source };
  for (const key of Object.keys(environment)) {
    if (
      sensitiveName.test(key) ||
      key.startsWith("CSC_") ||
      /^(?:ALL|HTTP|HTTPS)_PROXY$/i.test(key)
    ) {
      delete environment[key];
    }
  }
  return environment;
}
