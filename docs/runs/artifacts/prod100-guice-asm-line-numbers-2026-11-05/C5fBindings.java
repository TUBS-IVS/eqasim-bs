package org.eqasim.mode_choice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.stream.Collectors;

import com.google.inject.Injector;

/**
 * Scratch harness of the C5f investigation (not part of the repository): builds the real eqasim injector and prints the
 * number and a SHA-256 of the sorted keys of its explicit bindings, to compare the binding sets of two Guice variants.
 */
public class C5fBindings {
	public static void main(String[] args) throws Exception {
		Injector injector = TripEstimatorBindingTest.createInjector();

		List<String> keys = injector.getBindings().keySet().stream().map(Object::toString).sorted()
				.collect(Collectors.toList());
		MessageDigest digest = MessageDigest.getInstance("SHA-256");
		byte[] hash = digest.digest(String.join("\n", keys).getBytes(StandardCharsets.UTF_8));
		StringBuilder hex = new StringBuilder();
		for (byte b : hash) {
			hex.append(String.format("%02x", b));
		}
		System.out.println("C5F_BINDINGS=" + keys.size() + " C5F_KEYS_SHA256=" + hex);
		System.exit(0);
	}
}
