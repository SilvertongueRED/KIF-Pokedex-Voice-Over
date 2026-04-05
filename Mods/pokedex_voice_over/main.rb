# =============================================================================
# Pokédex Voice Over — KIF Mod
# =============================================================================
# Reads Pokédex entries aloud in the style of Dexter, the classic Pokémon
# anime Pokédex narrator, whenever a Pokédex entry page is opened.
#
# Supports both regular Pokémon and every fused variant.
#
# AUDIO FILES
# -----------
# Audio files must be placed in:
#   <game root>/Audio/SE/
#
# Naming convention:
#   dex_SPECIES.ogg              — regular Pokémon  (e.g. dex_BULBASAUR.ogg)
#   dex_SPECIES1_SPECIES2.ogg    — fused Pokémon    (e.g. dex_BULBASAUR_CHARMANDER.ogg)
#
# Run  tools/generate_voices.py  from the repository to generate these files
# from your game's PBS data.
# =============================================================================

module PokedexVoiceOver
  MOD_ID = "pokedex_voice_over"

  # -------------------------------------------------------------------------
  # Settings helpers
  # -------------------------------------------------------------------------

  def self.settings
    ($mod_manager_settings || {})[MOD_ID] || {}
  end

  def self.enabled?
    settings.fetch("enabled", true)
  end

  def self.volume
    settings.fetch("volume", 80).to_i.clamp(0, 100)
  end

  def self.play_on_page_change?
    settings.fetch("play_on_page_change", false)
  end

  # -------------------------------------------------------------------------
  # Species helpers
  # -------------------------------------------------------------------------

  # Convert a species value (Symbol, Integer, or String) to an UPPER_CASE
  # internal-name string suitable for use in a filename.
  def self.species_str(species)
    return nil if species.nil?
    # Integer 0 means "no species" in older Essentials versions
    return nil if species.is_a?(Integer) && species <= 0

    if species.is_a?(Symbol)
      return species.to_s.upcase
    end

    if species.is_a?(Integer)
      # Try to resolve via GameData (Essentials v19+)
      begin
        return GameData::Species.get(species).id.to_s.upcase
      rescue StandardError
        nil
      end
      # Fallback: try PBSpecies constant name (Essentials v18)
      begin
        name = getConstantName(PBSpecies, species)
        return name.upcase if name
      rescue StandardError
        nil
      end
      return species.to_s
    end

    return species.to_s.upcase
  end

  # -------------------------------------------------------------------------
  # Playback
  # -------------------------------------------------------------------------

  # Play the voice-over for *species* (and optionally its fusion partner).
  # Silently returns if no matching audio file is found.
  def self.play(species, fused_species = nil)
    return unless enabled?

    base = species_str(species)
    return unless base

    fused = species_str(fused_species)

    # Bare filename passed to Audio.se_play — RGSS auto-prepends Audio/SE/
    bare = if fused
             "dex_#{base}_#{fused}"
           else
             "dex_#{base}"
           end

    # FileTest.exist? uses the real filesystem path (RGSS does NOT auto-prepend
    # here), so check the full Audio/SE/ path with an explicit extension.
    found = [".ogg", ".wav", ".mp3"].any? do |ext|
      FileTest.exist?("Audio/SE/#{bare}#{ext}")
    end
    return unless found

    begin
      # Stop any currently running SE so voice-overs don't overlap
      Audio.se_stop
      # SE (Sound Effect) is used for playback; RGSS resolves the file from
      # Audio/SE/ automatically when no directory prefix is included.
      Audio.se_play(bare, volume, 100)
    rescue StandardError => e
      p "[PokedexVoiceOver] Audio playback error: #{e.message}" if $DEBUG
    end
  end

  # Stop any playing voice-over.
  def self.stop
    begin
      Audio.se_stop
    rescue StandardError => e
      p "[PokedexVoiceOver] Audio stop error: #{e.message}" if $DEBUG
    end
  end
end

# =============================================================================
# Hook into PokemonPokedexInfo_Scene
# =============================================================================
# We alias three methods:
#   pbStartScene  — fires once when the Pokédex entry scene opens
#   pbShowPage    — fires whenever the displayed page changes
#   pbEndScene    — fires when the scene closes (so we can stop the audio)
#
# The description/entry page is typically page index 0 in Pokémon Infinite
# Fusion.  Adjust ENTRY_PAGE below if your build uses a different index.
# =============================================================================

if defined?(PokemonPokedexInfo_Scene)
  class PokemonPokedexInfo_Scene
    POKEDEX_VO_ENTRY_PAGE = 0   # page index that shows the Pokédex description

    # Helper: read the current species + fusion partner from scene state.
    # Tries several variable names used across PIF / KIF versions.
    def pokedex_vo_current_species
      head  = @species rescue nil
      # Fusion partner: different KIF / PIF versions use different names
      fused = begin
                @fusedSpecies
              rescue StandardError
                begin
                  @dexSpecies2
                rescue StandardError
                  begin
                    @speciesFused
                  rescue StandardError
                    nil
                  end
                end
              end
      [head, fused]
    end

    # ------------------------------------------------------------------
    # pbStartScene  — play when the Pokédex entry scene first opens
    # ------------------------------------------------------------------
    if method_defined?(:pbStartScene)
      alias dex_vo_orig_pbStartScene pbStartScene

      def pbStartScene(*args)
        dex_vo_orig_pbStartScene(*args)
        # After the scene is set up, play the voice for the entry page
        page = @page rescue POKEDEX_VO_ENTRY_PAGE
        if page == POKEDEX_VO_ENTRY_PAGE
          head, fused = pokedex_vo_current_species
          PokedexVoiceOver.play(head, fused)
        end
      end
    end

    # ------------------------------------------------------------------
    # pbShowPage  — play (optionally) when returning to the entry page
    # ------------------------------------------------------------------
    if method_defined?(:pbShowPage)
      alias dex_vo_orig_pbShowPage pbShowPage

      def pbShowPage(page, *args)
        prev_page = @page rescue nil
        dex_vo_orig_pbShowPage(page, *args)

        # Only play when the user navigates TO the entry page, not on every
        # call.  We also honour the "play_on_page_change" setting so players
        # who do not want the voice to restart on every revisit can turn it
        # off.
        if page == POKEDEX_VO_ENTRY_PAGE && prev_page != POKEDEX_VO_ENTRY_PAGE
          if PokedexVoiceOver.play_on_page_change?
            head, fused = pokedex_vo_current_species
            PokedexVoiceOver.play(head, fused)
          end
        end
      end
    end

    # ------------------------------------------------------------------
    # pbEndScene  — stop the voice when the scene closes
    # ------------------------------------------------------------------
    if method_defined?(:pbEndScene)
      alias dex_vo_orig_pbEndScene pbEndScene

      def pbEndScene(*args)
        PokedexVoiceOver.stop
        dex_vo_orig_pbEndScene(*args)
      end
    end
  end
end
