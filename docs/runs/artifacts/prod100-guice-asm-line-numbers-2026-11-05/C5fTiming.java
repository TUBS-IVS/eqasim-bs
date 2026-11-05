package org.eqasim.mode_choice;

/**
 * Scratch harness of the C5f investigation (not part of the repository): builds the real eqasim injector once in a fresh
 * JVM, the way TripEstimatorBindingTest does (InjectorBuilder -> Controler.getInjector -> MATSim Injector.printInjector),
 * and prints the wall time of that step. Lives in the test package to reach the package-private createInjector().
 */
public class C5fTiming {
	public static void main(String[] args) throws Exception {
		long start = System.nanoTime();
		TripEstimatorBindingTest.createInjector();
		long end = System.nanoTime();
		System.out.println("C5F_INJECTOR_MS=" + Math.round((end - start) / 1e6));
		System.exit(0);
	}
}
