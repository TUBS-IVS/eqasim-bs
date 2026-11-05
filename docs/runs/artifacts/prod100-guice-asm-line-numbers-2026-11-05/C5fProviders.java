package org.eqasim.mode_choice;

import java.util.Map;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.google.inject.Binding;
import com.google.inject.Injector;

/**
 * Scratch harness of the C5f investigation (not part of the repository): all provider methods of the real eqasim
 * injector grouped by the first two package elements of the declaring class, with the number that has no source line.
 */
public class C5fProviders {
	public static void main(String[] args) throws Exception {
		Injector injector = TripEstimatorBindingTest.createInjector();
		Pattern provider = Pattern.compile("Provides\\S*\\s+([\\w.$]+)\\.([\\w$<>]+)\\(([^)]*)\\)");
		Pattern resolved = Pattern.compile("[\\w$]+\\.java:\\d+");

		Map<String, String> methods = new TreeMap<>();
		for (Binding<?> binding : injector.getBindings().values()) {
			for (String text : new String[] { String.valueOf(binding), String.valueOf(binding.getProvider()),
					String.valueOf(binding.getSource()) }) {
				Matcher m = provider.matcher(text);
				while (m.find()) {
					methods.put(m.group(1) + "." + m.group(2), m.group(3));
				}
			}
		}

		Map<String, int[]> byPrefix = new TreeMap<>();
		for (Map.Entry<String, String> e : methods.entrySet()) {
			String[] parts = e.getKey().split("\\.");
			String prefix = parts.length >= 2 ? parts[0] + "." + parts[1] : parts[0];
			int[] counts = byPrefix.computeIfAbsent(prefix, k -> new int[2]);
			counts[0]++;
			if (!resolved.matcher(e.getValue()).matches()) {
				counts[1]++;
			}
		}
		System.out.println("C5F_PROVIDER_METHODS_TOTAL=" + methods.size());
		byPrefix.forEach((prefix, c) -> System.out.println("C5F_PREFIX " + prefix + " methods=" + c[0] + " unresolved=" + c[1]));
		System.exit(0);
	}
}
