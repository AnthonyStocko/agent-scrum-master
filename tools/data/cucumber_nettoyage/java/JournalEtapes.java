package cucumber.nettoyage;

import io.cucumber.plugin.ConcurrentEventListener;
import io.cucumber.plugin.event.DataTableArgument;
import io.cucumber.plugin.event.EventPublisher;
import io.cucumber.plugin.event.PickleStepTestStep;
import io.cucumber.plugin.event.StepArgument;
import io.cucumber.plugin.event.TestStepStarted;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Plugin Cucumber (déclaré dans le RunnerTest du projet) : mémorise le step en cours d'exécution (texte et table)
 * pour le hook de nettoyage, y compris pour les steps de la bibliothèque du Standard. Installé par l'agent ; ne
 * modifier que dans l'agent (tools/data/cucumber_nettoyage).
 */
public class JournalEtapes implements ConcurrentEventListener {

    /** Step en cours sur ce fil d'exécution (les steps d'un scénario s'exécutent sur un seul fil). */
    static final ThreadLocal<EtapeCourante> COURANTE = new ThreadLocal<>();

    @Override
    public void setEventPublisher(EventPublisher publisher) {
        publisher.registerHandlerFor(TestStepStarted.class, event -> {
            if (!(event.getTestStep() instanceof PickleStepTestStep)) {
                return;
            }
            PickleStepTestStep step = (PickleStepTestStep) event.getTestStep();
            StepArgument argument = step.getStep().getArgument();
            List<List<String>> cells = argument instanceof DataTableArgument
                    ? new ArrayList<>(((DataTableArgument) argument).cells()) : Collections.emptyList();
            COURANTE.set(new EtapeCourante(step.getStep().getText(), cells));
        });
    }

    /** Step en cours : texte et cellules de sa table (première ligne = en-têtes). */
    public static final class EtapeCourante {
        public final String texte;
        public final List<List<String>> cellules;

        EtapeCourante(String texte, List<List<String>> cellules) {
            this.texte = texte;
            this.cellules = cellules;
        }
    }
}
